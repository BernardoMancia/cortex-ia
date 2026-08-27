"""
Script utilitário para sincronizar todo o código de produção do Córtex IA
com o diretório do repositório Git (f:/Projetos/cortex-ia/GIT) e enviar (push) ao GitHub.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

SRC_DIR = Path(r"f:\Projetos\cortex-ia")
GIT_DIR = Path(r"f:\Projetos\cortex-ia\GIT")

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "GIT",
    "logs",
    "data",
    ".idea",
    ".vscode",
}

EXCLUDE_FILES = {
    ".env",
    "simulator_state.json",
    "cortex.db",
    "logs.db",
    "cortex-ia.tar.gz",
}

def sync_directories():
    print(f"[*] Sincronizando arquivos de {SRC_DIR} para {GIT_DIR}...")
    
    if not GIT_DIR.exists():
        print(f"[ERRO] Diretório GIT não encontrado em {GIT_DIR}")
        sys.exit(1)
        
    copied_count = 0
    for root, dirs, files in os.walk(SRC_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        
        rel_dir = os.path.relpath(root, SRC_DIR)
        target_dir = GIT_DIR if rel_dir == "." else GIT_DIR / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for file in files:
            if file in EXCLUDE_FILES or file.endswith((".pyc", ".log", ".db", ".sqlite", ".tar.gz", ".bak")):
                continue
            if file.startswith(("check_", "test_", "fix_", "grep_", "temp_", "ps_aux", "allow_port", "get_", "force_reset", "reset_data", "update_env")) and rel_dir == ".":
                continue
            if file.endswith(".txt") and file != "requirements.txt" and rel_dir == ".":
                continue

            src_file = Path(root) / file
            dst_file = target_dir / file
            
            if not dst_file.exists() or src_file.stat().st_mtime > dst_file.stat().st_mtime or src_file.stat().st_size != dst_file.stat().st_size:
                shutil.copy2(src_file, dst_file)
                copied_count += 1

    print(f"[OK] {copied_count} arquivo(s) sincronizado(s) com o repositório GIT.")

def git_commit_and_push(commit_msg: str, tag: str | None = None):
    print("[*] Verificando status do Git...")
    
    subprocess.run(["git", "-C", str(GIT_DIR), "add", "."], check=True)
    
    res = subprocess.run(["git", "-C", str(GIT_DIR), "status", "--porcelain"], capture_output=True, text=True)
    if not res.stdout.strip():
        print("[INFO] Nenhuma alteração pendente para commit no Git.")
    else:
        print(f"[*] Criando commit: \"{commit_msg}\"...")
        subprocess.run(["git", "-C", str(GIT_DIR), "commit", "-m", commit_msg], check=True)
        
    if tag:
        print(f"[*] Criando tag de versão: {tag}...")
        subprocess.run(["git", "-C", str(GIT_DIR), "tag", "-a", tag, "-m", f"Release {tag}"], check=False)
        print("[*] Enviando tags...")
        subprocess.run(["git", "-C", str(GIT_DIR), "push", "origin", "--tags"], check=False)

    print("[*] Enviando alterações para o GitHub (origin/main)...")
    push_res = subprocess.run(["git", "-C", str(GIT_DIR), "push", "origin", "main"], capture_output=True, text=True)
    print(push_res.stdout)
    if push_res.stderr:
        print(push_res.stderr)
    print("[SUCESSO] Repositório sincronizado e enviado ao GitHub!")

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "release(v2.5.0): update core system and mobile-first dashboard"
    version_tag = sys.argv[2] if len(sys.argv) > 2 else "v2.5.0"
    
    sync_directories()
    git_commit_and_push(msg, tag=version_tag)
