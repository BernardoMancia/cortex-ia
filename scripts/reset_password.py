#!/usr/bin/env python3
"""
=============================================================================
CÓRTEX IA — Utilitário CLI para Redefinição de Senhas e Acesso de Emergência
=============================================================================
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from data.database import DatabaseManager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Redefinição de Senha do Córtex IA")
    parser.add_argument(
        "--username",
        type=str,
        default="Admin",
        help="Nome de usuário (Padrão: Admin)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Nova senha a ser definida",
    )
    parser.add_argument(
        "--force-first-login",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Exigir troca de senha no próximo login "
            "(Padrão: False ao definir senha, True em --restore-factory-default)"
        ),
    )
    parser.add_argument(
        "--restore-factory-default",
        action="store_true",
        help="Restaura usuário Admin e senha Admin com troca obrigatória no primeiro login",
    )

    args = parser.parse_args()
    db = DatabaseManager()

    if args.restore_factory_default:
        username = "Admin"
        password = "Admin"
        force_first = True if args.force_first_login is None else bool(args.force_first_login)
        print("[*] Restaurando credenciais de fábrica (Admin / Admin)...")
    else:
        username = args.username.strip()
        if not username:
            print("[ERRO] O nome de usuário não pode ser vazio!", file=sys.stderr)
            return 1

        password = args.password
        force_first = False if args.force_first_login is None else bool(args.force_first_login)

        if not password:
            print(f"[*] Redefinindo senha para o usuário: {username}")
            password = getpass.getpass("Digite a nova senha: ")
            confirm = getpass.getpass("Confirme a nova senha: ")
            if not password:
                print("[ERRO] A senha não pode ser vazia!", file=sys.stderr)
                return 1
            if password != confirm:
                print("[ERRO] As senhas não coincidem!", file=sys.stderr)
                return 1

    user = db.get_user_by_username(username)
    if not user:
        print(f"[*] Usuário '{username}' não encontrado. Criando novo usuário...")
        db.create_user(username, password, must_change_password=force_first)
        print(f"[OK] Usuário '{username}' criado com sucesso!")
    else:
        db.reset_user_password(username, password, force_first_login=force_first)
        db.reset_user_login_failures(user["id"])
        db.revoke_all_user_sessions(user["id"])
        print(f"[OK] Senha do usuário '{username}' redefinida e bloqueios de login limpos!")

    print("")
    print("+------------------------------------------------------------+")
    print(f"  Usuário: {username}")
    print(f"  Senha:   {'******' if not args.restore_factory_default else 'Admin'}")
    print(f"  Exigir Primeiro Acesso: {'SIM' if force_first else 'NÃO'}")
    print("+------------------------------------------------------------+")
    print("[SUCESSO] Você já pode acessar o Dashboard com as novas credenciais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
