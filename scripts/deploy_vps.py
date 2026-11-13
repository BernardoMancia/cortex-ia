from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time

import paramiko
from scp import SCPClient

DEFAULT_PROD_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOMOLOG_ROOT = DEFAULT_PROD_ROOT.parent / "cortex-ia-homolog"

PACKAGE_EXCLUDED_DIRS: frozenset[str] = frozenset(
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
        "mobile",
        "mobile_charts",
    }
)

PACKAGE_EXCLUDED_SUFFIXES: frozenset[str] = frozenset(
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
        ".apk",
        ".aab",
        ".jks",
        ".keystore",
        ".zip",
        ".gz",
        ".tar",
    }
)


@dataclass(frozen=True)
class DeployTargetSpec:
    key: str
    slug: str
    service_name: str
    port: int
    local_root: Path
    description: str


def resolve_targets(
    target_arg: str,
    prod_root: Path = DEFAULT_PROD_ROOT,
    homolog_root: Path = DEFAULT_HOMOLOG_ROOT,
) -> list[DeployTargetSpec]:
    prod_spec = DeployTargetSpec(
        key="prod",
        slug="cortex-ia",
        service_name="cortex-ia",
        port=8003,
        local_root=prod_root,
        description="Córtex IA - Produção Real B3",
    )
    homolog_spec = DeployTargetSpec(
        key="homolog",
        slug="cortex-ia-homolog",
        service_name="cortex-ia-homolog",
        port=9003,
        local_root=homolog_root,
        description="Córtex IA - Homologação Simulada B3",
    )
    choice = target_arg.strip().lower()
    if choice == "prod":
        return [prod_spec]
    if choice == "homolog":
        return [homolog_spec]
    if choice == "both":
        return [homolog_spec, prod_spec]
    raise ValueError(f"Alvo inválido: {target_arg}")


def read_vps_credentials_from_env() -> tuple[str, int, str, str]:
    host = os.environ.get("VPS_HOST", "").strip()
    port_raw = os.environ.get("VPS_PORT", "22").strip() or "22"
    user = os.environ.get("VPS_USER", "").strip()
    password = os.environ.get("VPS_PASS", "")
    if not host or not user or not password:
        raise RuntimeError(
            "Credenciais ausentes: defina VPS_HOST, VPS_PORT, VPS_USER e VPS_PASS nas variáveis de ambiente."
        )
    return host, int(port_raw), user, password


def should_package_file(rel: Path) -> bool:
    if any(part in PACKAGE_EXCLUDED_DIRS for part in rel.parts):
        return False
    name = rel.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return False
    lower = name.lower()
    if any(lower.endswith(suf) for suf in PACKAGE_EXCLUDED_SUFFIXES):
        return False
    return True


def build_clean_tarball(local_root: Path, slug: str) -> Path:
    if not local_root.exists():
        raise FileNotFoundError(f"Diretório local não encontrado para {slug}: {local_root}")
    fd, tmp_name = tempfile.mkstemp(prefix=f"{slug}_", suffix=".tar.gz")
    os.close(fd)
    archive_path = Path(tmp_name)
    with tarfile.open(archive_path, mode="w:gz") as tf:
        for file_path in sorted(local_root.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(local_root)
            if not should_package_file(rel):
                continue
            raw_bytes = file_path.read_bytes()
            info = tarfile.TarInfo(name=rel.as_posix())
            info.size = len(raw_bytes)
            info.mtime = int(file_path.stat().st_mtime)
            info.mode = 0o755 if file_path.suffix == ".sh" else 0o644
            info.uid = 0
            info.gid = 0
            tf.addfile(info, io.BytesIO(raw_bytes))
    return archive_path


def render_systemd_unit(spec: DeployTargetSpec, remote_dir: str, vps_user: str) -> str:
    central_log_dir = f"/LOGS-PROJETOS/{spec.slug}"
    venv_dir = f"{remote_dir}/.venv"
    lines = [
        "[Unit]",
        f"Description={spec.description}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={vps_user}",
        f"Group={vps_user}",
        f"WorkingDirectory={remote_dir}",
        f"EnvironmentFile=-{remote_dir}/.env",
        f'Environment="PATH={venv_dir}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"',
        f"ExecStartPre=/bin/mkdir -p {central_log_dir}",
        f"ExecStart={venv_dir}/bin/python main.py",
        "Restart=always",
        "RestartSec=10",
        "TimeoutStopSec=90",
        "StartLimitIntervalSec=60",
        "StartLimitBurst=3",
        f"StandardOutput=append:{central_log_dir}/systemd-out.log",
        f"StandardError=append:{central_log_dir}/systemd-error.log",
        "UMask=0077",
        "LimitNOFILE=65535",
        "ProtectSystem=strict",
        f"ReadWritePaths={remote_dir} {central_log_dir}",
        "PrivateTmp=true",
        "NoNewPrivileges=true",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def _exec_remote(
    ssh: paramiko.SSHClient,
    command: str,
    sudo_password: str | None = None,
    timeout: int = 180,
) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()
    stdin.channel.shutdown_write()
    out_text = stdout.read().decode("utf-8", errors="replace")
    err_text = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, out_text, err_text


def deploy_single_target(
    ssh: paramiko.SSHClient,
    spec: DeployTargetSpec,
    vps_user: str,
    vps_pass: str,
) -> None:
    remote_dir = f"/home/{vps_user}/{spec.slug}"
    central_log_dir = f"/LOGS-PROJETOS/{spec.slug}"
    remote_tar = f"/tmp/{spec.slug}_deploy.tar.gz"
    remote_unit_tmp = f"/tmp/{spec.service_name}.service"

    archive_path = build_clean_tarball(spec.local_root, spec.slug)
    try:
        with SCPClient(ssh.get_transport()) as scp_client:
            scp_client.put(str(archive_path), remote_tar)

            unit_bytes = render_systemd_unit(spec, remote_dir, vps_user).encode("utf-8")
            scp_client.putfo(io.BytesIO(unit_bytes), remote_unit_tmp)
    finally:
        archive_path.unlink(missing_ok=True)

    extract_and_perm_cmd = (
        f"set -e; "
        f"mkdir -p '{remote_dir}' '{remote_dir}/data' '{remote_dir}/data/.keys' "
        f"'{remote_dir}/backups' '{remote_dir}/logs'; "
        f"if [ -f '{remote_dir}/.env' ]; then cp -p '{remote_dir}/.env' '/tmp/{spec.slug}.env.keep'; chmod 600 '/tmp/{spec.slug}.env.keep'; fi; "
        f"tar -xzf '{remote_tar}' -C '{remote_dir}'; "
        f"rm -f '{remote_tar}'; "
        f"if [ -f '/tmp/{spec.slug}.env.keep' ]; then mv '/tmp/{spec.slug}.env.keep' '{remote_dir}/.env'; "
        f"elif [ ! -f '{remote_dir}/.env' ] && [ -f '{remote_dir}/.env.example' ]; then cp '{remote_dir}/.env.example' '{remote_dir}/.env'; fi; "
        f"chmod 700 '{remote_dir}/data' '{remote_dir}/data/.keys' '{remote_dir}/backups'; "
        f"if [ -f '{remote_dir}/.env' ]; then chmod 600 '{remote_dir}/.env'; fi; "
        f"find '{remote_dir}/data' -maxdepth 2 -type f -exec chmod 600 {{}} + 2>/dev/null || true; "
        f"if [ ! -x '{remote_dir}/.venv/bin/python' ]; then python3 -m venv '{remote_dir}/.venv'; fi; "
        f"grep -viE '^\\s*metatrader5' '{remote_dir}/requirements.txt' | '{remote_dir}/.venv/bin/python' -m pip install -q -r /dev/stdin"
    )
    rc, _, err = _exec_remote(ssh, extract_and_perm_cmd, timeout=300)
    if rc != 0:
        raise RuntimeError(f"Falha ao preparar arquivos/venv em {remote_dir}: {err}")

    sudo_setup_cmd = (
        f"sudo -S -p '' bash -c \""
        f"mkdir -p '{central_log_dir}' && "
        f"chown -R {vps_user}:{vps_user} '{central_log_dir}' && "
        f"mv '{remote_unit_tmp}' '/etc/systemd/system/{spec.service_name}.service' && "
        f"chmod 644 '/etc/systemd/system/{spec.service_name}.service' && "
        f"systemctl daemon-reload && "
        f"systemctl enable '{spec.service_name}.service' && "
        f"systemctl restart '{spec.service_name}.service'\""
    )
    rc_s, _, err_s = _exec_remote(ssh, sudo_setup_cmd, sudo_password=vps_pass, timeout=120)
    if rc_s != 0:
        raise RuntimeError(f"Falha ao configurar systemd/logs para {spec.service_name}: {err_s}")

    healthy = False
    health_check_cmd = (
        f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{spec.port}/login"
    )
    for _ in range(20):
        time.sleep(2.0)
        rc_h, out_h, _ = _exec_remote(ssh, health_check_cmd, timeout=15)
        code_str = out_h.strip()[:3]
        if rc_h == 0 and code_str in ("200", "302", "307", "401"):
            healthy = True
            break

    if not healthy:
        raise RuntimeError(f"Health check HTTP falhou na porta {spec.port} para {spec.service_name}.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deploy_vps")
    parser.add_argument("--target", choices=("prod", "homolog", "both"), default="prod")
    parser.add_argument("--prod-root", type=Path, default=DEFAULT_PROD_ROOT)
    parser.add_argument("--homolog-root", type=Path, default=DEFAULT_HOMOLOG_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    targets = resolve_targets(args.target, prod_root=args.prod_root, homolog_root=args.homolog_root)

    if args.dry_run:
        for spec in targets:
            tar_path = build_clean_tarball(spec.local_root, spec.slug)
            size_kb = round(tar_path.stat().st_size / 1024.0, 1)
            tar_path.unlink(missing_ok=True)
            sys.stdout.write(
                f"[DRY-RUN] Pacote validado para {spec.slug} (porta {spec.port}): {size_kb} KB\n"
            )
        return 0

    host, port, user, password = read_vps_credentials_from_env()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            timeout=20,
            look_for_keys=False,
            allow_agent=False,
        )
        for spec in targets:
            sys.stdout.write(f"Iniciando deploy remoto de {spec.slug} (porta {spec.port})...\n")
            deploy_single_target(ssh, spec, vps_user=user, vps_pass=password)
            sys.stdout.write(f"Deploy e health check aprovados para {spec.slug}.\n")
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
