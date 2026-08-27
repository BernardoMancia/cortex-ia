#!/usr/bin/env python3
"""
=============================================================================
CÓRTEX IA — Universal Auto-Installer & Virtual Environment Setup
Suporta: Windows (10/11/Server), Linux (Ubuntu/Debian/CentOS/Fedora) e macOS.
=============================================================================
"""

import os
import sys
import platform
import subprocess
import shutil
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WINDOWS = platform.system().lower() == "windows"

def color(text: str, code: str) -> str:
    if IS_WINDOWS and not os.getenv("WT_SESSION") and not os.getenv("TERM"):
        return text
    return f"\033[{code}m{text}\033[0m"

def log_info(msg: str):
    print(f"{color('[INFO]', '36')} {msg}")

def log_ok(msg: str):
    print(f"{color('[OK]', '32')} {msg}")

def log_warn(msg: str):
    print(f"{color('[AVISO]', '33')} {msg}")

def log_error(msg: str):
    print(f"{color('[ERRO]', '31')} {msg}")

def main():
    print("")
    print(color("+============================================================+", "36"))
    print(color("|       CORTEX IA -- INSTALADOR UNIVERSAL DE AMBIENTE        |", "36"))
    print(color("|       Compativel com Windows, Linux, macOS e Servidores    |", "36"))
    print(color("+============================================================+", "36"))
    print("")

    root_dir = Path(__file__).resolve().parent
    venv_dir = root_dir / ".venv"

    py_ver = sys.version_info
    log_info(f"Sistema Operacional: {platform.system()} {platform.release()} ({platform.machine()})")
    log_info(f"Interpretador Python: {sys.executable} (v{py_ver.major}.{py_ver.minor}.{py_ver.micro})")

    if py_ver < (3, 10):
        log_error("O Cortex IA requer Python 3.10 ou superior!")
        log_warn("Por favor, instale uma versao mais recente do Python (recomendado 3.12).")
        sys.exit(1)

    if not venv_dir.exists() or not (venv_dir / "pyvenv.cfg").exists():
        log_info(f"Criando ambiente virtual em: {venv_dir}...")
        try:
            import venv
            venv.create(venv_dir, with_pip=True)
            log_ok("Ambiente virtual (.venv) criado com sucesso.")
        except Exception as exc:
            log_warn(f"Modulo venv embutido reportou: {exc}. Tentando via subprocess...")
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
            log_ok("Ambiente virtual (.venv) criado via subprocess.")
    else:
        log_ok(f"Ambiente virtual ja existente em: {venv_dir}")

    if IS_WINDOWS:
        venv_python = venv_dir / "Scripts" / "python.exe"
        venv_pip = venv_dir / "Scripts" / "pip.exe"
    else:
        venv_python = venv_dir / "bin" / "python"
        venv_pip = venv_dir / "bin" / "pip"

    if not venv_python.exists():
        log_error(f"Executavel Python nao encontrado no ambiente: {venv_python}")
        sys.exit(1)

    log_info("Atualizando pip, setuptools e wheel...")
    subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "--quiet"], check=False)
    log_ok("Gerenciador de pacotes pip atualizado.")

    req_file = root_dir / "requirements.txt"
    if req_file.exists():
        log_info("Instalando dependencias de requirements.txt...")
        
        if not IS_WINDOWS:
            temp_req = root_dir / ".temp_requirements_linux.txt"
            lines = req_file.read_text(encoding="utf-8").splitlines()
            filtered = [l for l in lines if not l.strip().lower().startswith("metatrader5")]
            temp_req.write_text("\n".join(filtered), encoding="utf-8")
            
            install_cmd = [str(venv_pip), "install", "-r", str(temp_req)]
            res = subprocess.run(install_cmd)
            if temp_req.exists():
                temp_req.unlink()
        else:
            install_cmd = [str(venv_pip), "install", "-r", str(req_file)]
            res = subprocess.run(install_cmd)

        if res.returncode == 0:
            log_ok("Todas as dependencias foram instaladas com sucesso!")
        else:
            log_warn("Algumas dependencias podem ter falhado. Verifique as mensagens acima.")
    else:
        log_warn("Arquivo requirements.txt nao encontrado.")

    log_info("Verificando diretorios de execucao...")
    for folder in ["data", "logs", "backups"]:
        p = root_dir / folder
        p.mkdir(parents=True, exist_ok=True)
    log_ok("Diretorios 'data/', 'logs/' e 'backups/' verificados.")

    env_file = root_dir / ".env"
    env_example = root_dir / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy2(env_example, env_file)
        log_ok("Arquivo '.env' criado a partir de '.env.example'.")
    elif env_file.exists():
        log_ok("Arquivo '.env' ja configurado.")

    log_info("Validando importacoes dos modulos do Cortex IA...")
    test_code = """
import core.engine
import dashboard.app
import data.database
import analysis.technical
import analysis.sentiment
import config.settings
print('[TESTE OK] Todos os modulos carregados com sucesso.')
"""
    test_res = subprocess.run([str(venv_python), "-c", test_code], capture_output=True, text=True)
    if test_res.returncode == 0:
        log_ok("Verificacao concluida: O Cortex IA esta 100% pronto para execucao!")
    else:
        log_warn(f"Aviso na verificacao de modulos:\n{test_res.stderr}")

    print("")
    print(color("------------------------------------------------------------", "32"))
    print(color("[SUCESSO] INSTALACAO DO AMBIENTE CONCLUIDA COM SUCESSO!", "32"))
    print(color("------------------------------------------------------------", "32"))
    print("")
    print("Para iniciar o Cortex IA:")
    if IS_WINDOWS:
        print(f"  * Ativar ambiente:  {color('.\\.venv\\Scripts\\activate', '36')}")
        print(f"  * Iniciar robo:     {color('.\\.venv\\Scripts\\python main.py', '36')}")
    else:
        print(f"  * Ativar ambiente:  {color('source .venv/bin/activate', '36')}")
        print(f"  * Iniciar robo:     {color('./.venv/bin/python main.py', '36')}")
        print(f"  * Ou via servico:   {color('sudo systemctl start cortex-ia', '36')}")
    print("")

if __name__ == "__main__":
    main()
