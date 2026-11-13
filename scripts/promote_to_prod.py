from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import io
from pathlib import Path
import shutil
import sys
import tokenize
import zipfile
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")

DEFAULT_PROD_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOMOLOG_ROOT = DEFAULT_PROD_ROOT.parent / "cortex-ia-homolog"

ALLOWED_SHARED_DIRS: frozenset[str] = frozenset(
    {
        "bi",
        "config",
        "domain",
        "security",
        "observability",
        "notifications",
        "powerbi",
        "quant",
        "engine",
        "storage",
        "market",
        "wallets",
        "web",
        "scripts",
    }
)

FORBIDDEN_EXACT_REL_PATHS: frozenset[str] = frozenset(
    {
        ".env",
        "config/env_profile.py",
        "wallets/capital_service.py",
        "storage/capital_repo.py",
        "market/providers/mt5_prov.py",
        "web/routers/capital_routes.py",
    }
)

FORBIDDEN_TOP_DIRS: frozenset[str] = frozenset(
    {
        "brokers",
        "infra",
        "backtest",
        "mobile",
        "mobile_charts",
        "data",
        "backups",
        "logs",
        "GIT",
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
    }
)

BACKUP_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        "GIT",
        ".git",
        "backups",
        "logs",
        ".gradle",
        "build",
    }
)


def is_promotable_rel_path(rel_posix: str) -> bool:
    clean = rel_posix.strip().lstrip("/").replace("\\", "/")
    if not clean:
        return False
    parts = clean.split("/")
    if any(p in ("__pycache__", ".pytest_cache", ".git", ".venv") for p in parts):
        return False
    if clean.endswith((".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3", ".enc", ".key", ".apk", ".aab")):
        return False
    if parts[0].startswith(".env"):
        return False
    if parts[0] in FORBIDDEN_TOP_DIRS:
        return False
    if clean in FORBIDDEN_EXACT_REL_PATHS:
        return False
    return parts[0] in ALLOWED_SHARED_DIRS


def verify_python_zero_comments(py_path: Path, display_rel: str) -> list[str]:
    issues: list[str] = []
    text = py_path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            issues.append(f"{display_rel}:{tok.start[0]} contem comentario '#' proibido")
            break
    tree = ast.parse(text, filename=display_rel)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                lineno = getattr(node, "lineno", 1)
                issues.append(f"{display_rel}:{lineno} contem docstring proibida")
                break
    return issues


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_prod_version(prod_root: Path) -> str:
    settings_path = prod_root / "config" / "settings.py"
    if not settings_path.exists():
        return "0.1.0"
    try:
        tree = ast.parse(settings_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
                if node.target.attr == "VERSION" and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
    except Exception:
        pass
    return "0.1.0"


def create_prod_backup_zip(prod_root: Path) -> Path:
    backups_dir = prod_root / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    version = _extract_prod_version(prod_root)
    stamp = datetime.now(tz=BRT).strftime("%Y-%m-%d_%H%M%S")
    zip_path = backups_dir / f"backup_v{version}_pre_promotion_{stamp}.zip"
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in prod_root.rglob("*"):
            if not item.is_file():
                continue
            rel_parts = item.relative_to(prod_root).parts
            if any(p in BACKUP_SKIP_DIRS for p in rel_parts):
                continue
            zf.write(item, arcname=item.relative_to(prod_root).as_posix())
    return zip_path


def collect_promotion_candidates(source_root: Path, target_root: Path) -> tuple[list[str], list[str]]:
    changed_or_new: list[str] = []
    violations: list[str] = []
    for src_file in sorted(source_root.rglob("*")):
        if not src_file.is_file():
            continue
        rel_posix = src_file.relative_to(source_root).as_posix()
        if not is_promotable_rel_path(rel_posix):
            continue
        if rel_posix in FORBIDDEN_EXACT_REL_PATHS or rel_posix.split("/")[0] in FORBIDDEN_TOP_DIRS:
            violations.append(f"Caminho bloqueado detectado: {rel_posix}")
            continue
        if src_file.suffix.lower() == ".py":
            violations.extend(verify_python_zero_comments(src_file, rel_posix))
        dst_file = target_root / rel_posix
        if not dst_file.exists() or _sha256_file(src_file) != _sha256_file(dst_file):
            changed_or_new.append(rel_posix)
    return changed_or_new, violations


def run_promotion(
    source_root: Path,
    target_root: Path,
    confirm_authorized: bool,
    dry_run: bool = False,
) -> int:
    if not confirm_authorized:
        sys.stderr.write(
            "BLOQUEADO PELA RULE[user_global] Seção 5 (Proteção Estrita de Produção vs. Homologação):\n"
            "A promoção de arquivos da Homologação para a Produção jamais pode ser executada sem "
            "autorização explícita do usuário ('aplique a homologação na produção').\n"
            "Quando expressamente autorizado pelo usuário, execute com a flag "
            "--confirm-authorized-promotion (e opcionalmente --dry-run).\n"
        )
        return 2

    if not source_root.exists() or not source_root.is_dir():
        sys.stderr.write(f"Diretório de Homologação não encontrado: {source_root}\n")
        return 1
    if not target_root.exists() or not target_root.is_dir():
        sys.stderr.write(f"Diretório de Produção não encontrado: {target_root}\n")
        return 1
    if source_root.resolve() == target_root.resolve():
        sys.stderr.write("Origem e destino não podem ser o mesmo diretório.\n")
        return 1

    candidates, violations = collect_promotion_candidates(source_root, target_root)
    if violations:
        sys.stderr.write("Falha no Gate Zero-Comments / Proteção de Escopo:\n")
        for v in violations:
            sys.stderr.write(f"  - {v}\n")
        return 1

    if not candidates:
        sys.stdout.write("Nenhum arquivo compartilhado divergente entre Homologação e Produção.\n")
        return 0

    if dry_run:
        sys.stdout.write(f"[DRY-RUN] {len(candidates)} arquivo(s) elegível(is) para promoção:\n")
        for rel in candidates:
            sys.stdout.write(f"  ~ {rel}\n")
        return 0

    backup_zip = create_prod_backup_zip(target_root)
    sys.stdout.write(f"Backup pré-promoção criado: {backup_zip}\n")

    for rel in candidates:
        if rel in FORBIDDEN_EXACT_REL_PATHS or rel.split("/")[0] in FORBIDDEN_TOP_DIRS:
            raise RuntimeError(f"Tentativa bloqueada de sobrescrever arquivo proibido: {rel}")
        src = source_root / rel
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        sys.stdout.write(f"  + Promovido: {rel}\n")

    sys.stdout.write(f"Promoção concluída com sucesso ({len(candidates)} arquivo(s) sincronizado(s)).\n")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promote_to_prod")
    parser.add_argument("--source", type=Path, default=DEFAULT_HOMOLOG_ROOT)
    parser.add_argument("--target", type=Path, default=DEFAULT_PROD_ROOT)
    parser.add_argument("--confirm-authorized-promotion", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_promotion(
        source_root=args.source,
        target_root=args.target,
        confirm_authorized=bool(args.confirm_authorized_promotion),
        dry_run=bool(args.dry_run),
    )


if __name__ == "__main__":
    raise SystemExit(main())
