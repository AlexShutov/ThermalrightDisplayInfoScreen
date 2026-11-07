#!/usr/bin/env python3
"""Проверка «понятного кода»: детектирует нечитаемые имена идентификаторов.

Чистый AST-сканер без сторонних зависимостей. Ищет имена (функции, методы,
параметры, классы, переменные в присваиваниях/циклах/`with`/`except`/comprehension),
которые нарушают правило «имена объясняют себя сами»:

- числовые сокращения вида ``a0``/``a1``/``c1``/``c2``/``b0`` (латиница + цифра);
- имена из стоп-листа явно запрещённых сокращений (``bx0``, ``bw``, ``bh``, ``rr``,
  ``ang``, ``pts``, ``tmp``/``temp2``, ``flag``, ``val``, ``arg`` и т.п.);
- однобуквенное ``l`` (PEP 8 запрещает ``l``/``O``/``I``).

Сканер консервативный: он не флагует распространённые короткие, но понятные имена
(``i``/``j``/``k``, ``x``/``y``/``w``/``h``/``r``/``t``, ``cx``/``cy``, ``px``/``py``),
иначе он бы блокировал почти любой коммит. Запускать как pre-commit-гейт или вручную:

    python tools/check_names.py [путь ...]
    python tools/check_names.py --all
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Iterator
from pathlib import Path

# Имена, которые правило прямо запрещает (ярлык по смыслу, а не «что хранит»).
_FORBIDDEN_NAMES = frozenset(
    {
        "bx0",
        "bx1",
        "bw",
        "bh",
        "bv",
        "rr",
        "ang",
        "pts",
        "tmp",
        "temp2",
        "flag",
        "val",
        "arg",
        "dur",
    }
)

# Однобуквенные имена, которые PEP 8 запрещает (похожи на цифры / легко спутать).
_FORBIDDEN_SINGLES = frozenset({"l", "L", "O", "I"})

_NUMERIC_SHORTHAND = re.compile(r"^[A-Za-z]\d+\w*$")


def _name_offender(name: str) -> str | None:
    """Возвращает причину нарушения (или ``None``, если имя читаемое)."""
    if _NUMERIC_SHORTHAND.match(name):
        return f"числовое сокращение '{name}'"
    if name in _FORBIDDEN_NAMES:
        return f"запрещённое сокращение '{name}'"
    if name in _FORBIDDEN_SINGLES:
        return f"однобуквенное имя '{name}'"
    return None


def _iter_name_targets(target: ast.AST | None, lineno: int) -> Iterator[tuple[str, int]]:
    """Отдаёт имена из цели присваивания с номером строки выражения."""
    if isinstance(target, ast.Name):
        yield target.id, lineno
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _iter_name_targets(element, lineno)


def _iter_names(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Отдаёт ``(имя, номер_строки)`` для всех определяемых имён в дереве."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node.lineno
            params = (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
            for param in params:
                yield param.arg, getattr(param, "lineno", node.lineno)
            if node.args.vararg is not None:
                yield node.args.vararg.arg, getattr(node.args.vararg, "lineno", node.lineno)
            if node.args.kwarg is not None:
                yield node.args.kwarg.arg, getattr(node.args.kwarg, "lineno", node.lineno)
        elif isinstance(node, ast.ClassDef):
            yield node.name, node.lineno
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                yield from _iter_name_targets(target, node.lineno)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            yield from _iter_name_targets(node.target, node.lineno)
        elif isinstance(node, ast.With):
            for item in node.items:
                yield from _iter_name_targets(item.optional_vars, node.lineno)
        elif isinstance(node, ast.ExceptHandler):
            if node.name is not None:
                yield node.name, node.lineno
        elif isinstance(node, ast.comprehension):
            yield from _iter_name_targets(node.target, getattr(node, "lineno", 0))


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Сканирует один файл, возвращает ``(номер_строки, имя, причина)``."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[tuple[int, str, str]] = []
    for name, lineno in _iter_names(tree):
        reason = _name_offender(name)
        if reason:
            violations.append((lineno, name, reason))
    return violations


def _iter_py_files(paths: list[Path]) -> list[Path]:
    """Собирает все ``.py``-файлы по переданным путям (файл или каталог)."""
    out: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix == ".py":
                out.append(path)
        elif path.is_dir():
            for sub in path.rglob("*.py"):
                if "__pycache__" in sub.parts:
                    continue
                out.append(sub)
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None, help="файлы или каталоги")
    parser.add_argument("--all", action="store_true", help="сканировать весь репозиторий")
    args = parser.parse_args(argv)

    if args.all:
        paths = [Path("src"), Path("tools"), Path("tests"), Path("DashboardWidgets")]
    elif args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        parser.error("укажите пути или --all")

    total = 0
    for file in _iter_py_files(paths):
        for lineno, name, reason in _scan_file(file):
            total += 1
            print(f"{file}:{lineno}: {name} — {reason}")

    print(f"\nНайдено нарушений: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
