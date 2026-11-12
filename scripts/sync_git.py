#!/usr/bin/env python3
"""
Córtex IA v2.8.0 — Sanitized Git Repository Synchronizer (`scripts/sync_git.py`).

Synchronizes clean production source code from the workspace root into `GIT/`:
1. Preserves `data/*.py` (`__init__.py`, `database.py`, `market_data.py`, `news_scraper.py`)
   while strictly excluding `*.db*`, `*.sqlite*`, `*.json`, and `*.log`.
2. Excludes `backups`, `.venv`, `venv`, `logs`, `GIT`, `.git`, `__pycache__`,
   `.pytest_cache`, `scripts/tools`, `.env`, `resumo_chat.json`, `test_vps_state_run.py`.
3. Prunes stale files and empty directories in `GIT/` that were deleted from Root
   (preserving `GIT/.git` and `GIT/LICENSE`).
4. Scans every synced text file in `GIT/` to guarantee ZERO occurrences of real
   VPS IPs, SSH passwords, Google Vertex/Gemini API keys, or Telegram `bot` tokens.
5. Never runs `git push` automatically unless `--push` is explicitly passed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
GIT_DIR = SRC_DIR / "GIT"
DEFAULT_VERSION = "v2.9.0"
DEFAULT_COMMIT_MSG = (
    "release(v2.9.0): autonomous Docker MT5 Bridge, 0ms Bridge quotes, B3 spread/fee/D+2 & Anti-Day-Trade protections"
)

EXCLUDE_DIRS = {
    ".git",
    "GIT",
    "backups",
    ".venv",
    "venv",
    "env",
    "ENV",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".gemini",
    ".agent",
    ".agents",
    ".claude",
    "dist",
    "build",
    "htmlcov",
    "scratch",
    "tmp",
    "temp",
}

EXCLUDE_REL_DIRS = {
    Path("scripts") / "tools",
}

EXCLUDE_FILES = {
    ".env",
    "resumo_chat.json",
    "test_vps_state_run.py",
    "simulator_state.json",
    "prod_simulator_state.json",
    "cortex.db",
    "logs.db",
    "servers.dat",
    "cortex-ia.tar.gz",
    "Thumbs.db",
    ".DS_Store",
    "desktop.ini",
    "ehthumbs.db",
    ".coverage",
}

EXCLUDE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite",
    ".sqlite3",
    ".json",
    ".log",
    ".bak",
    ".tmp",
    ".swp",
    ".swo",
    ".zip",
    ".tar",
    ".tar.gz",
    ".gz",
    ".7z",
    ".rar",
    ".onnx",
    ".ex5",
    ".pt",
    ".pth",
    ".bin",
    ".h5",
)

ROOT_SCRATCH_PREFIXES = (
    "check_",
    "test_",
    "fix_",
    "grep_",
    "temp_",
    "ps_aux",
    "allow_port",
    "get_",
    "force_reset",
    "reset_data",
    "update_env",
    "audit_vps",
    "deploy_cache",
    "remote_engine",
    "start_remote",
)

# SHA-256 digests of known sensitive infrastructure values (avoids storing literals in source)
_FORBIDDEN_IP_SHA256 = {
    "fb2728c5360b8b1c883f498788402ad87ffef10780a3337f528b2eadd02858a0",
}
_FORBIDDEN_SECRET_SHA256 = {
    "fc70df2da9024de116d08f088187b563ee41f24c8f84b2cfeebf7b8bc303d66a",
}

_REGEX_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GOOGLE_AQ_API_KEY", re.compile(r"[A]Q\.[A]b8[A-Za-z0-9_\-]{8,}")),
    ("GOOGLE_AIZA_API_KEY", re.compile(r"[A]Iza[0-9A-Za-z_\-]{35}")),
    ("TELEGRAM_BOT_TOKEN", re.compile(r"(?:bot)?\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b")),
]
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_QUOTED_OR_ASSIGNED_RE = re.compile(
    r"""(?:\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*([^\s#'"]{8,128}))|['"]([^'"]{8,128})['"]"""
)


def should_sync_file(rel_path: Path) -> bool:
    parts = rel_path.parts
    if not parts:
        return False

    for part in parts[:-1]:
        if part in EXCLUDE_DIRS:
            return False

    parent_rel = rel_path.parent
    for excl_rel in EXCLUDE_REL_DIRS:
        if parent_rel == excl_rel or excl_rel in parent_rel.parents:
            return False

    filename = rel_path.name
    if filename in EXCLUDE_FILES and rel_path != Path("docker") / "servers.dat":
        return False
    if filename.startswith(".env.") and filename != ".env.example":
        return False

    # Inside data/, ONLY sync Python modules (data/*.py)
    if parts[0] == "data":
        return len(parts) == 2 and filename.endswith(".py")

    if filename.lower().endswith(EXCLUDE_SUFFIXES):
        return False

    if len(parts) == 1:
        if filename.startswith(ROOT_SCRATCH_PREFIXES):
            return False
        if filename.endswith(".txt") and filename != "requirements.txt":
            return False

    return True


def _load_dynamic_env_secrets(env_path: Path) -> dict[str, str]:
    """Loads sensitive values from local `.env` (if present) for leak detection."""
    sensitive_keys = {
        "DEPLOY_HOST",
        "DEPLOY_PASSWORD",
        "GEMINI_API_KEY",
        "TELEGRAM_TOKEN",
        "MT5_LOGIN",
        "MT5_PASSWORD",
        "BRIDGE_API_KEY",
        "VNC_PASSWORD",
    }
    safe_placeholders = {
        "",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "Admin",
        "seu_login",
        "sua_senha",
        "seu_token_aqui",
    }
    secrets_map: dict[str, str] = {}
    if not env_path.exists():
        return secrets_map

    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key in sensitive_keys and len(val) >= 6 and val not in safe_placeholders:
                secrets_map[key] = val
    except OSError:
        pass
    return secrets_map


def sync_directories(dry_run: bool = False) -> set[Path]:
    print(f"[*] Córtex IA {DEFAULT_VERSION} — Sincronizando {SRC_DIR} -> {GIT_DIR}...")

    if not GIT_DIR.exists():
        print(f"[ERRO] Diretório GIT não encontrado em {GIT_DIR}", file=sys.stderr)
        sys.exit(1)

    synced_rel_paths: set[Path] = set()
    copied_count = 0

    for root, dirs, files in os.walk(SRC_DIR):
        rel_root = Path(root).relative_to(SRC_DIR)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in EXCLUDE_DIRS
            and (rel_root / d) not in EXCLUDE_REL_DIRS
            and not (rel_root == Path("data"))
        )

        for fname in sorted(files):
            rel_path = Path(fname) if rel_root == Path(".") else rel_root / fname
            if not should_sync_file(rel_path):
                continue

            synced_rel_paths.add(rel_path)
            src_file = SRC_DIR / rel_path
            dst_file = GIT_DIR / rel_path

            needs_copy = (
                not dst_file.exists()
                or src_file.stat().st_size != dst_file.stat().st_size
                or int(src_file.stat().st_mtime) != int(dst_file.stat().st_mtime)
                or src_file.read_bytes() != dst_file.read_bytes()
            )
            if needs_copy:
                if dry_run:
                    print(f"  [SYNC] {rel_path.as_posix()}")
                else:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                copied_count += 1

    # Prune stale files in GIT/ that no longer exist in Root
    removed_count = 0
    for root, dirs, files in os.walk(GIT_DIR):
        dirs[:] = [d for d in dirs if d != ".git"]
        rel_root = Path(root).relative_to(GIT_DIR)
        for fname in files:
            rel_path = Path(fname) if rel_root == Path(".") else rel_root / fname
            # Preserve standalone LICENSE in GIT/ if Root doesn't keep a copy
            if rel_path == Path("LICENSE") and not (SRC_DIR / "LICENSE").exists():
                continue
            if rel_path not in synced_rel_paths:
                stale_target = GIT_DIR / rel_path
                if dry_run:
                    print(f"  [PRUNE] {rel_path.as_posix()}")
                else:
                    stale_target.unlink(missing_ok=True)
                removed_count += 1

    # Remove empty directories inside GIT/ (except .git)
    if not dry_run:
        for root, dirs, _ in os.walk(GIT_DIR, topdown=False):
            rel_root = Path(root).relative_to(GIT_DIR)
            if rel_root == Path(".") or ".git" in rel_root.parts:
                continue
            try:
                if not any(Path(root).iterdir()):
                    Path(root).rmdir()
            except OSError:
                pass

    print(
        f"[OK] Sincronização concluída: {copied_count} atualizado(s), "
        f"{removed_count} obsoleto(s) removido(s), {len(synced_rel_paths)} arquivo(s) rastreado(s)."
    )
    return synced_rel_paths


def verify_zero_secrets(target_dir: Path) -> None:
    """Scans all synced text files in `GIT/` and aborts on any secret or real VPS IP."""
    print("[*] Executando varredura de segurança (Zero-Secrets & Zero-IP Gate)...")
    dynamic_secrets = _load_dynamic_env_secrets(SRC_DIR / ".env")
    violations: list[str] = []

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fname in sorted(files):
            fpath = Path(root) / fname
            rel_str = fpath.relative_to(target_dir).as_posix()

            try:
                raw_bytes = fpath.read_bytes()
            except OSError as exc:
                violations.append(f"{rel_str}: erro ao ler arquivo ({exc})")
                continue

            if b"\x00" in raw_bytes:
                for env_key, secret_val in dynamic_secrets.items():
                    if (
                        secret_val.encode("utf-8") in raw_bytes
                        or secret_val.encode("utf-16le") in raw_bytes
                    ):
                        violations.append(
                            f"{rel_str}:binary -> Vazamento detectado de {env_key} (do .env)"
                        )
                continue

            text = raw_bytes.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for env_key, secret_val in dynamic_secrets.items():
                    if secret_val in line:
                        violations.append(
                            f"{rel_str}:{lineno} -> Vazamento detectado de {env_key} (do .env)"
                        )

                for label, pattern in _REGEX_SECRET_PATTERNS:
                    if pattern.search(line):
                        violations.append(
                            f"{rel_str}:{lineno} -> Padrão proibido detectado ({label})"
                        )

                for ip_match in _IPV4_RE.findall(line):
                    ip_hash = hashlib.sha256(ip_match.encode("utf-8")).hexdigest()
                    if ip_hash in _FORBIDDEN_IP_SHA256:
                        violations.append(
                            f"{rel_str}:{lineno} -> IP real de servidor proibido detectado"
                        )

                for grp_a, grp_b in _QUOTED_OR_ASSIGNED_RE.findall(line):
                    candidate = grp_a or grp_b
                    cand_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
                    if cand_hash in _FORBIDDEN_SECRET_SHA256:
                        violations.append(
                            f"{rel_str}:{lineno} -> Credencial/senha proibida detectada"
                        )

    if violations:
        print("\n[ERRO CRÍTICO DE SEGURANÇA] Vazamentos bloqueados antes do Git:", file=sys.stderr)
        for item in violations:
            print(f"  ✖ {item}", file=sys.stderr)
        sys.exit(1)

    print("[OK] Varredura concluída: ZERO segredos, senhas, chaves ou IPs reais encontrados.")


def git_stage_commit_and_push(
    commit_msg: str,
    *,
    tag: str | None = None,
    do_commit: bool = True,
    do_push: bool = False,
) -> None:
    if not (GIT_DIR / ".git").exists():
        print("[AVISO] Subdiretório GIT/.git não inicializado; etapa Git ignorada.")
        return

    print("[*] Verificando status do repositório GIT/...")
    subprocess.run(["git", "-C", str(GIT_DIR), "add", "-A"], check=True)

    status_res = subprocess.run(
        ["git", "-C", str(GIT_DIR), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    has_changes = bool(status_res.stdout.strip())

    if not has_changes:
        print("[INFO] Nenhuma alteração pendente no diretório GIT/.")
    elif do_commit:
        print(f'[*] Criando commit local: "{commit_msg}"...')
        subprocess.run(
            ["git", "-C", str(GIT_DIR), "commit", "-m", commit_msg], check=True
        )
    else:
        print("[INFO] Alterações preparadas no stage (--no-commit ativo).")

    if tag and do_commit:
        print(f"[*] Registrando tag local: {tag}...")
        subprocess.run(
            ["git", "-C", str(GIT_DIR), "tag", "-f", "-a", tag, "-m", f"Release {tag}"],
            check=False,
        )

    if not do_push:
        print("[INFO] Push remoto NÃO executado (use --push explicitamente para enviar ao GitHub).")
        return

    print("[*] Enviando alterações para o repositório remoto (origin)...")
    subprocess.run(["git", "-C", str(GIT_DIR), "push", "origin", "HEAD"], check=True)
    if tag:
        subprocess.run(["git", "-C", str(GIT_DIR), "push", "origin", tag], check=False)
    print("[SUCESSO] Repositório sincronizado e enviado ao GitHub!")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Sincronizador sanitizado Córtex IA ({DEFAULT_VERSION}) -> GIT/"
    )
    parser.add_argument(
        "positional_msg",
        nargs="?",
        default=None,
        help="Mensagem de commit opcional (posicional).",
    )
    parser.add_argument(
        "-m",
        "--message",
        default=None,
        help=f"Mensagem do commit (padrão: '{DEFAULT_COMMIT_MSG}').",
    )
    parser.add_argument(
        "-t",
        "--tag",
        default=None,
        help="Tag opcional de versão (ex: v2.8.0).",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=False,
        help="Envia commits/tags ao remoto GitHub (desativado por padrão).",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        default=False,
        help="Apenas sincroniza e valida os arquivos em GIT/ sem criar commit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simula a sincronização sem alterar arquivos em GIT/.",
    )

    args = parser.parse_args()
    commit_msg = args.message or args.positional_msg or DEFAULT_COMMIT_MSG

    sync_directories(dry_run=args.dry_run)
    if args.dry_run:
        return 0

    verify_zero_secrets(GIT_DIR)
    git_stage_commit_and_push(
        commit_msg,
        tag=args.tag,
        do_commit=not args.no_commit,
        do_push=args.push,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
