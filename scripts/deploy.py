#!/usr/bin/env python3
"""
Córtex IA — Safe Remote VPS Deployment Script (v2.8.0).

Security & Data-Protection Guarantees:
- Reads `DEPLOY_HOST`, `DEPLOY_USER`, and `DEPLOY_SSH_KEY` (preferred) or
  `DEPLOY_PASSWORD` strictly from environment / `.env` (zero hardcoded IPs or secrets).
- Verifies SSH host keys via system/user `known_hosts` (`RejectPolicy` by default;
  opt-in `DEPLOY_ALLOW_UNKNOWN_HOST=true` only when explicitly requested).
- Never passes passwords on remote shell command lines (`echo ... | sudo -S` is forbidden).
- Strictly excludes `.env`, `.git`, `GIT`, `backups`, `logs`, `.venv`, `venv`,
  `__pycache__`, `.pytest_cache`, `scripts/tools`, `resumo_chat.json`, and
  within `data/` ONLY uploads `data/*.py` — live `data/*.db*` and `data/*.json`
  on the VPS can NEVER be overwritten.
- Creates a remote backup on the VPS (`backups/`) BEFORE updating any files.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import paramiko
from dotenv import load_dotenv
from scp import SCPClient

ROOT_DIR = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    ".git",
    "GIT",
    "backups",
    "logs",
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
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
    "cortex.db",
    "logs.db",
    "simulator_state.json",
    "prod_simulator_state.json",
    "cortex-ia.tar.gz",
    "Thumbs.db",
    ".DS_Store",
    "desktop.ini",
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
    ".zip",
    ".tar",
    ".tar.gz",
    ".gz",
    ".7z",
    ".rar",
)


def should_include_file(rel_path: Path) -> bool:
    parts = rel_path.parts
    if not parts:
        return False

    # Check excluded directory names anywhere in path
    for part in parts[:-1]:
        if part in EXCLUDE_DIRS:
            return False

    # Check excluded relative subpaths (e.g. scripts/tools)
    parent_rel = rel_path.parent
    for excl_rel in EXCLUDE_REL_DIRS:
        if parent_rel == excl_rel or excl_rel in parent_rel.parents:
            return False

    filename = rel_path.name
    if filename in EXCLUDE_FILES:
        return False
    if filename.startswith(".env.") and filename != ".env.example":
        return False

    # CRITICAL: Inside data/, ONLY allow Python source modules (data/*.py)
    if parts[0] == "data":
        return len(parts) == 2 and filename.endswith(".py")

    if filename.lower().endswith(EXCLUDE_SUFFIXES):
        return False

    # Exclude root diagnostic .txt files while keeping requirements.txt
    if len(parts) == 1 and filename.endswith(".txt") and filename != "requirements.txt":
        return False

    return True


def collect_deploy_files(source_dir: Path) -> list[Path]:
    collected: list[Path] = []
    for root, dirs, files in os.walk(source_dir):
        rel_root = Path(root).relative_to(source_dir)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in EXCLUDE_DIRS
            and (rel_root / d) not in EXCLUDE_REL_DIRS
            and not (rel_root == Path("data"))
        )
        for fname in sorted(files):
            rel_file = fname if rel_root == Path(".") else rel_root / fname
            rel_path = Path(rel_file)
            if should_include_file(rel_path):
                collected.append(rel_path)
    return collected


def build_release_tarball(source_dir: Path, files: list[Path], archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz") as tar:
        for rel_path in files:
            abs_path = source_dir / rel_path
            arcname = rel_path.as_posix()
            # Safety assertion: never allow forbidden runtime artifacts into archive
            if arcname == ".env" or arcname.startswith(("GIT/", "backups/", "logs/", "scripts/tools/")):
                raise RuntimeError(f"Bloqueio de segurança: caminho proibido no pacote: {arcname}")
            if arcname.startswith("data/") and not arcname.endswith(".py"):
                raise RuntimeError(
                    f"Bloqueio de segurança: arquivo de dados proibido no pacote: {arcname}"
                )
            tar.add(str(abs_path), arcname=arcname)


def configure_host_keys(ssh: paramiko.SSHClient) -> None:
    ssh.load_system_host_keys()
    user_known_hosts = Path.home() / ".ssh" / "known_hosts"
    if user_known_hosts.exists():
        ssh.load_host_keys(str(user_known_hosts))

    allow_unknown = os.getenv("DEPLOY_ALLOW_UNKNOWN_HOST", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if allow_unknown:
        ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
    else:
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())


def run_remote(
    ssh: paramiko.SSHClient,
    command: str,
    *,
    check: bool = True,
    sudo_password: str | None = None,
) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=180)
    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()
    stdin.close()
    exit_code = stdout.channel.recv_exit_status()
    out_text = stdout.read().decode("utf-8", errors="replace")
    err_text = stderr.read().decode("utf-8", errors="replace")
    if check and exit_code != 0:
        raise RuntimeError(
            f"Comando remoto falhou (exit={exit_code}): {command}\nSTDOUT: {out_text}\nSTDERR: {err_text}"
        )
    return exit_code, out_text, err_text


def run_remote_sudo(
    ssh: paramiko.SSHClient, command: str, password: str
) -> tuple[int, str, str]:
    # Prefer passwordless sudo (-n) first so no secret ever crosses stdin unless required
    code, out, err = run_remote(ssh, f"sudo -n {command}", check=False)
    if code == 0:
        return code, out, err
    if not password:
        raise RuntimeError(
            f"sudo sem senha indisponível na VPS para '{command}' e DEPLOY_PASSWORD não foi fornecida."
        )
    return run_remote(ssh, f"sudo -S -p '' {command}", check=True, sudo_password=password)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy seguro do Córtex IA para a VPS (preserva banco e estados)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista os arquivos que seriam enviados sem conectar na VPS.",
    )
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help="Atualiza arquivos e dependências sem reiniciar o serviço systemd.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT_DIR / ".env")

    files = collect_deploy_files(ROOT_DIR)
    print(f"[INFO] {len(files)} arquivos selecionados para deploy seguro.")

    if args.dry_run:
        for rel in files:
            print(f"  + {rel.as_posix()}")
        return 0

    host = os.getenv("DEPLOY_HOST", "").strip()
    port = int(os.getenv("DEPLOY_PORT", "22").strip() or "22")
    user = os.getenv("DEPLOY_USER", "").strip()
    ssh_key = os.getenv("DEPLOY_SSH_KEY", "").strip()
    password = os.getenv("DEPLOY_PASSWORD", "").strip()
    remote_dir = os.getenv("DEPLOY_REMOTE_DIR", "/home/servico/cortex-ia").strip()

    if not host or not user:
        print(
            "[ERRO] DEPLOY_HOST e DEPLOY_USER devem estar definidos no ambiente ou .env.",
            file=sys.stderr,
        )
        return 1
    if not ssh_key and not password:
        print(
            "[ERRO] Defina DEPLOY_SSH_KEY (recomendado) ou DEPLOY_PASSWORD no ambiente.",
            file=sys.stderr,
        )
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    quoted_remote = shlex.quote(remote_dir)

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_archive = Path(tmp_dir) / f"cortex_release_{timestamp}.tar.gz"
        build_release_tarball(ROOT_DIR, files, local_archive)

        print(f"[INFO] Conectando a {user}@{host}:{port}...")
        ssh = paramiko.SSHClient()
        configure_host_keys(ssh)

        connect_kwargs: dict[str, object] = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": 20,
        }
        if ssh_key:
            connect_kwargs["key_filename"] = str(Path(ssh_key).expanduser())
        if password:
            connect_kwargs["password"] = password

        ssh.connect(**connect_kwargs)
        try:
            print("[1/5] Garantindo estrutura remota e executando backup pré-deploy...")
            backup_cmd = (
                f"mkdir -p {quoted_remote}/backups {quoted_remote}/data {quoted_remote}/logs && "
                f"if [ -f {quoted_remote}/data/cortex.db ]; then "
                f"  if command -v sqlite3 >/dev/null 2>&1; then "
                f"    sqlite3 {quoted_remote}/data/cortex.db \".backup '{remote_dir}/backups/pre_deploy_cortex_{timestamp}.db'\"; "
                f"  else "
                f"    cp -p {quoted_remote}/data/cortex.db {quoted_remote}/backups/pre_deploy_cortex_{timestamp}.db; "
                f"  fi; "
                f"fi && "
                f"if [ -f {quoted_remote}/data/simulator_state.json ]; then "
                f"  cp -p {quoted_remote}/data/simulator_state.json {quoted_remote}/backups/pre_deploy_simulator_state_{timestamp}.json; "
                f"fi && "
                f"if [ -f {quoted_remote}/data/prod_simulator_state.json ]; then "
                f"  cp -p {quoted_remote}/data/prod_simulator_state.json {quoted_remote}/backups/pre_deploy_prod_simulator_state_{timestamp}.json; "
                f"fi && "
                f"tar -czf {quoted_remote}/backups/pre_deploy_code_{timestamp}.tar.gz "
                f"--exclude='./backups' --exclude='./.venv' --exclude='./venv' "
                f"--exclude='./logs' --exclude='./data/*.db*' --exclude='./data/*.json' "
                f"-C {quoted_remote} . 2>/dev/null || true"
            )
            run_remote(ssh, backup_cmd, check=True)

            remote_tmp_archive = f"/tmp/cortex_release_{timestamp}.tar.gz"
            print("[2/5] Enviando pacote de código via SCP...")
            with SCPClient(ssh.get_transport()) as scp:
                scp.put(str(local_archive), remote_path=remote_tmp_archive)

            print("[3/5] Extraindo código atualizado e ajustando permissões de scripts...")
            extract_cmd = (
                f"tar -xzf {shlex.quote(remote_tmp_archive)} -C {quoted_remote} && "
                f"rm -f {shlex.quote(remote_tmp_archive)} && "
                f"find {quoted_remote} -maxdepth 3 -name '*.sh' -exec sed -i 's/\\r$//' {{}} + && "
                f"chmod +x {quoted_remote}/*.sh {quoted_remote}/scripts/*.sh {quoted_remote}/deploy/*.sh 2>/dev/null || true"
            )
            run_remote(ssh, extract_cmd, check=True)

            print("[4/5] Atualizando dependências Python no .venv remoto...")
            pip_cmd = (
                f"if [ ! -x {quoted_remote}/.venv/bin/python ]; then "
                f"  python3 -m venv {quoted_remote}/.venv; "
                f"fi && "
                f"{quoted_remote}/.venv/bin/python -m pip install --prefer-binary -q -r {quoted_remote}/requirements.txt"
            )
            run_remote(ssh, pip_cmd, check=True)

            if not args.skip_restart:
                print("[5/5] Atualizando unidade systemd e reiniciando cortex-ia.service...")
                run_remote_sudo(
                    ssh,
                    f"sh -c 'mkdir -p /LOGS-PROJETOS/cortex-ia && chown {shlex.quote(user)}:{shlex.quote(user)} /LOGS-PROJETOS/cortex-ia'",
                    password,
                )
                run_remote_sudo(
                    ssh,
                    f"cp {quoted_remote}/scripts/cortex-ia.service /etc/systemd/system/cortex-ia.service",
                    password,
                )
                run_remote_sudo(ssh, "systemctl daemon-reload", password)
                run_remote_sudo(ssh, "systemctl enable cortex-ia.service", password)
                run_remote_sudo(ssh, "systemctl restart cortex-ia.service", password)
                _, status_out, _ = run_remote(
                    ssh, "systemctl status cortex-ia.service --no-pager -n 12 || true", check=False
                )
                print(status_out)
            else:
                print("[5/5] Reinicialização ignorada (--skip-restart).")
        finally:
            ssh.close()

    print("[SUCESSO] Deploy concluído com segurança!")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
