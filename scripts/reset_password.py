#!/usr/bin/env python3
"""
=============================================================================
CÓRTEX IA — Utilitário CLI para Redefinição de Senhas e Acesso de Emergência
=============================================================================
"""

import sys
import argparse
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from data.database import DatabaseManager
from auth import PasswordManager

def main():
    parser = argparse.ArgumentParser(description="Redefinição de Senha do Córtex IA")
    parser.add_argument("--username", type=str, default="Admin", help="Nome de usuário (Padrão: Admin)")
    parser.add_argument("--password", type=str, default=None, help="Nova senha a ser definida")
    parser.add_argument("--force-first-login", action="store_true", default=True, help="Exigir troca no próximo login")
    parser.add_argument("--restore-factory-default", action="store_true", help="Restaura usuário Admin e senha Admin")

    args = parser.parse_args()
    db = DatabaseManager()

    if args.restore_factory_default:
        username = "Admin"
        password = "Admin"
        force_first = True
        print("[*] Restaurando credenciais de fábrica (Admin / Admin)...")
    else:
        username = args.username.strip()
        password = args.password
        force_first = args.force_first_login

        if not password:
            print(f"[*] Redefinindo senha para o usuário: {username}")
            import getpass
            password = getpass.getpass("Digite a nova senha: ")
            confirm = getpass.getpass("Confirme a nova senha: ")
            if password != confirm:
                print("[ERRO] As senhas não coincidem!")
                sys.exit(1)

    user = db.get_user_by_username(username)
    if not user:
        print(f"[*] Usuário '{username}' não encontrado. Criando novo usuário...")
        db.create_user(username, password, must_change_password=force_first)
        print(f"[OK] Usuário '{username}' criado com sucesso!")
    else:
        db.reset_user_password(username, password, force_first_login=force_first)
        db.revoke_all_user_sessions(user["id"])
        print(f"[OK] Senha do usuário '{username}' redefinida com sucesso!")

    print("")
    print("+------------------------------------------------------------+")
    print(f"  Usuário: {username}")
    print(f"  Senha:   {'******' if not args.restore_factory_default else 'Admin'}")
    print(f"  Exigir Primeiro Acesso: {'SIM' if force_first else 'NÃO'}")
    print("+------------------------------------------------------------+")
    print("[SUCESSO] Você já pode acessar o Dashboard com as novas credenciais.")

if __name__ == "__main__":
    main()
