from __future__ import annotations

import argparse
import ast
import hashlib
import io
from pathlib import Path
import re
import shutil
import sys
import tokenize

DEFAULT_SOURCE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GIT_WORKTREE = DEFAULT_SOURCE_ROOT / "GIT"

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "GIT",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "backups",
        "logs",
        "data",
        ".gradle",
        ".kotlin",
        "build",
        "keystore",
        "releases",
    }
)

EXCLUDED_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".db",
        ".db-wal",
        ".db-shm",
        ".sqlite",
        ".sqlite3",
        ".enc",
        ".key",
        ".pem",
        ".jks",
        ".keystore",
        ".apk",
        ".aab",
        ".zip",
        ".gz",
        ".tar",
        ".ex5",
        ".log",
    }
)

EXCLUDED_EXACT_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        "local.properties",
        "keystore.properties",
        "servers.dat",
        "Thumbs.db",
        ".DS_Store",
    }
)

TEXT_SCAN_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".yml",
        ".yaml",
        ".sh",
        ".bat",
        ".ps1",
        ".service",
        ".html",
        ".css",
        ".js",
        ".kt",
        ".kts",
        ".xml",
        ".pro",
        ".mq5",
        ".pq",
        ".dax",
        ".pbids",
        ".example",
    }
)

_OCT_1 = str(41 * 2)
_OCT_2 = str(56 * 2)
_VPS_IP_GATE_RE = re.compile(r"\b" + _OCT_1 + r"\." + _OCT_2 + r"\.\d{1,3}\.\d{1,3}\b")

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Telegram Bot Token", re.compile(r"\b(?:bot)?\d{7,12}:AA[A-Za-z0-9_-]{30,}\b")),
    ("Google/Gemini API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("Google AQ Key", re.compile(r"\bAQ\.Ab8[0-9A-Za-z_-]{8,}\b")),
    ("Private Key PEM", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Cortex BI Bearer Token", re.compile(r"\bctx_bi_[A-Za-z0-9_-]{32,}\b")),
)


def should_include_path(rel_path: Path) -> bool:
    parts = rel_path.parts
    if not parts:
        return False
    if any(p in EXCLUDED_DIRS for p in parts):
        return False
    name = rel_path.name
    if name in EXCLUDED_EXACT_NAMES:
        return False
    if name.startswith(".env") and name != ".env.example":
        return False
    lower_name = name.lower()
    if any(lower_name.endswith(suf) for suf in EXCLUDED_SUFFIXES):
        return False
    return True


def scan_text_for_secrets_and_ip(text: str, display_rel: str) -> list[str]:
    findings: list[str] = []
    ip_match = _VPS_IP_GATE_RE.search(text)
    if ip_match is not None:
        line_no = text.count("\n", 0, ip_match.start()) + 1
        findings.append(f"{display_rel}:{line_no} bloqueado pelo Zero-VPS-IP Gate")
    for label, pattern in _SECRET_PATTERNS:
        m = pattern.search(text)
        if m is not None:
            line_no = text.count("\n", 0, m.start()) + 1
            findings.append(f"{display_rel}:{line_no} bloqueado pelo Zero-Secrets Gate ({label})")
    return findings


def scan_python_zero_comments(text: str, display_rel: str) -> list[str]:
    findings: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            findings.append(f"{display_rel}:{tok.start[0]} bloqueado pelo Zero-Comments Gate ('#')")
            break
    tree = ast.parse(text, filename=display_rel)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                lineno = getattr(node, "lineno", 1)
                findings.append(f"{display_rel}:{lineno} bloqueado pelo Zero-Comments Gate (docstring)")
                break
    return findings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_scannable_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SCAN_SUFFIXES:
        return True
    return path.name in (
        "Dockerfile",
        ".gitignore",
        ".dockerignore",
        ".env.example",
        "LICENSE",
    )


def sync_to_git_worktree(
    source_root: Path,
    git_dir: Path,
    dry_run: bool = False,
    prune: bool = True,
) -> int:
    if not source_root.exists():
        sys.stderr.write(f"Diretório fonte inexistente: {source_root}\n")
        return 1

    included_files: list[Path] = []
    gate_violations: list[str] = []

    for item in sorted(source_root.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(source_root)
        if not should_include_path(rel):
            continue
        rel_posix = rel.as_posix()
        if _is_scannable_text_file(item):
            try:
                content = item.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = item.read_text(encoding="utf-8", errors="ignore")
            gate_violations.extend(scan_text_for_secrets_and_ip(content, rel_posix))
            if item.suffix.lower() == ".py":
                gate_violations.extend(scan_python_zero_comments(content, rel_posix))
        included_files.append(rel)

    if gate_violations:
        sys.stderr.write("Sincronização GIT abortada pelos Gates de Segurança/Qualidade:\n")
        for err in gate_violations:
            sys.stderr.write(f"  [BLOQUEIO] {err}\n")
        return 1

    expected_set = {r.as_posix() for r in included_files}
    to_copy: list[Path] = []
    for rel in included_files:
        dst = git_dir / rel
        src = source_root / rel
        if not dst.exists() or _sha256(src) != _sha256(dst):
            to_copy.append(rel)

    to_remove: list[Path] = []
    if prune and git_dir.exists():
        for existing in sorted(git_dir.rglob("*")):
            if not existing.is_file():
                continue
            rel_existing = existing.relative_to(git_dir)
            if ".git" in rel_existing.parts:
                continue
            if rel_existing.name == ".gitattributes":
                continue
            if rel_existing.as_posix() not in expected_set:
                to_remove.append(rel_existing)

    if dry_run:
        sys.stdout.write(
            f"[DRY-RUN] Gates aprovados ({len(included_files)} arquivos auditados). "
            f"Atualizar: {len(to_copy)}, Remover obsoletos: {len(to_remove)}.\n"
        )
        return 0

    git_dir.mkdir(parents=True, exist_ok=True)
    for rel in to_copy:
        src = source_root / rel
        dst = git_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel_rm in to_remove:
        target = git_dir / rel_rm
        target.unlink(missing_ok=True)

    sys.stdout.write(
        f"GIT worktree sincronizado com sucesso: {len(to_copy)} copiados, "
        f"{len(to_remove)} obsoletos removidos, 0 segredos/IPs/comentários.\n"
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync_git")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--git-dir", type=Path, default=DEFAULT_GIT_WORKTREE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-prune", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return sync_to_git_worktree(
        source_root=args.source,
        git_dir=args.git_dir,
        dry_run=bool(args.dry_run),
        prune=not bool(args.no_prune),
    )


if __name__ == "__main__":
    raise SystemExit(main())
