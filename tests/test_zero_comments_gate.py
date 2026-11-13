from __future__ import annotations

import ast
import io
from pathlib import Path
import tokenize

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MOBILE_MODULE_DIRS = (
    "mobile",
    "mobile_charts",
)

SKIP_DIRS = {
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "GIT",
    ".git",
    "backups",
    "data",
    "logs",
    ".gradle",
    ".kotlin",
    "build",
    "wrapper",
    "releases",
}


def _iter_project_files():
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(PROJECT_ROOT).parts
        if any(p in SKIP_DIRS for p in rel_parts):
            continue
        if path.name in ("gradlew", "gradlew.bat"):
            continue
        yield path


def _has_c_style_comments(
    source: str,
    is_mql5: bool = False,
    allow_single_quote: bool = True,
) -> str | None:
    n = len(source)
    i = 0
    in_single = False
    in_double = False
    in_template = False
    line_num = 1

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if (i + 1) < n else ""
        if ch == "\n":
            line_num += 1
            i += 1
            continue

        if in_single:
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if in_template:
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                in_template = False
            i += 1
            continue

        if allow_single_quote and ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if not is_mql5 and ch == "`":
            in_template = True
            i += 1
            continue

        if ch == "/" and nxt == "/":
            return f"line {line_num}: '//' comment found"
        if ch == "/" and nxt == "*":
            return f"line {line_num}: '/* */' block comment found"
        i += 1
    return None


def test_zero_python_comments_and_docstrings():
    violations: list[str] = []
    for path in _iter_project_files():
        if path.suffix != ".py":
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                violations.append(f"{rel}:{tok.start[0]} -> Python '#' comment")
                break
        tree = ast.parse(text, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node, clean=False) is not None:
                    lineno = getattr(node, "lineno", 1)
                    violations.append(f"{rel}:{lineno} -> Python docstring found")
                    break
    assert not violations, "Violations in Python Zero-Comments Gate:\n" + "\n".join(violations)


def test_zero_web_mql5_kotlin_and_config_comments():
    violations: list[str] = []
    hash_config_names = {
        "Dockerfile",
        "docker-compose.yml",
        "requirements.txt",
        ".env.example",
        "pytest.ini",
        ".gitignore",
        ".dockerignore",
        "gradle.properties",
        "libs.versions.toml",
    }
    for mob_dir in MOBILE_MODULE_DIRS:
        _ = PROJECT_ROOT / mob_dir

    for path in _iter_project_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        suf = path.suffix.lower()
        if suf in (".js", ".css", ".kt", ".kts", ".mq5"):
            text = path.read_text(encoding="utf-8")
            err = _has_c_style_comments(text, is_mql5=(suf == ".mq5"))
            if err:
                violations.append(f"{rel} -> {err}")
        elif suf == ".pq":
            text = path.read_text(encoding="utf-8")
            err = _has_c_style_comments(text, is_mql5=True, allow_single_quote=False)
            if err:
                violations.append(f"{rel} -> {err}")
        elif suf == ".dax":
            text = path.read_text(encoding="utf-8")
            err = _has_c_style_comments(text, is_mql5=True, allow_single_quote=True)
            if err:
                violations.append(f"{rel} -> {err}")
            for idx, ln in enumerate(text.splitlines(), start=1):
                if ln.lstrip().startswith("--"):
                    violations.append(f"{rel}:line {idx}: '--' DAX comment found")
                    break
        elif suf in (".html", ".xml"):
            text = path.read_text(encoding="utf-8")
            if "<!--" in text:
                violations.append(f"{rel} -> HTML/XML '<!--' comment found")
        elif suf == ".sh":
            lines = path.read_text(encoding="utf-8").splitlines()
            for idx, ln in enumerate(lines, start=1):
                stripped = ln.strip()
                if idx == 1 and stripped.startswith("#!"):
                    continue
                if stripped.startswith("#"):
                    violations.append(f"{rel}:{idx} -> shell '#' comment found")
                    break
        elif suf in (".service", ".pro") or path.name in hash_config_names:
            lines = path.read_text(encoding="utf-8").splitlines()
            for idx, ln in enumerate(lines, start=1):
                if ln.lstrip().startswith("#"):
                    violations.append(f"{rel}:{idx} -> config '#' comment found")
                    break
    assert not violations, "Violations in Multi-Language Zero-Comments Gate:\n" + "\n".join(violations)
