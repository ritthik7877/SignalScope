"""Static discovery of Django signal receivers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ReceiverRecord:
    """A statically discovered signal receiver."""

    signal: str
    sender: str | None
    receiver: str
    file: str
    line: int


def scan_app_paths(app_paths: Iterable[str | Path]) -> list[ReceiverRecord]:
    """Scan Python files under app paths for receiver decorators and connect calls."""
    records: list[ReceiverRecord] = []
    for app_path in app_paths:
        root = Path(app_path).resolve()
        if not root.is_dir() or _is_site_packages(root):
            continue
        for source_file in sorted(root.rglob("*.py")):
            if _is_site_packages(source_file):
                continue
            try:
                source = source_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(source_file))
            except (OSError, UnicodeError, SyntaxError) as error:
                raise ValueError(f"Unable to scan Python source {source_file}: {error}") from error
            records.extend(_ReceiverVisitor(source_file).scan(tree))
    return records


def scan_installed_apps() -> list[ReceiverRecord]:
    """Scan installed Django applications, excluding third-party site-packages."""
    from django.apps import apps

    return scan_app_paths(config.path for config in apps.get_app_configs())


def scan_project(app_paths: Iterable[str | Path] | None = None) -> list[ReceiverRecord]:
    """Scan explicit app paths, or all installed apps when paths are omitted."""
    if app_paths is None:
        return scan_installed_apps()
    return scan_app_paths(app_paths)


class _ReceiverVisitor(ast.NodeVisitor):
    def __init__(self, source_file: Path) -> None:
        self.source_file = source_file
        self.records: list[ReceiverRecord] = []
        self.function_stack: list[str] = []

    def scan(self, tree: ast.AST) -> list[ReceiverRecord]:
        self.visit(tree)
        return self.records

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            record = self._decorator_record(decorator, node.name)
            if record is not None:
                self.records.append(record)
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            record = self._decorator_record(decorator, node.name)
            if record is not None:
                self.records.append(record)
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "connect":
            signal = _expression_name(node.func.value)
            receiver_node = node.args[0] if node.args else _keyword_value(node, "receiver")
            receiver = _expression_name(receiver_node) if receiver_node is not None else None
            if signal and receiver:
                self.records.append(
                    ReceiverRecord(
                        signal=signal,
                        sender=_keyword_name(node, "sender"),
                        receiver=receiver,
                        file=str(self.source_file),
                        line=node.lineno,
                    )
                )
        self.generic_visit(node)

    def _decorator_record(self, node: ast.expr, function_name: str) -> ReceiverRecord | None:
        if not isinstance(node, ast.Call):
            return None
        decorator_name = _expression_name(node.func)
        if decorator_name is None or decorator_name.split(".")[-1] != "receiver":
            return None
        signal_node = node.args[0] if node.args else _keyword_value(node, "signal")
        signal = _expression_name(signal_node) if signal_node is not None else None
        if signal is None:
            return None
        qualified_name = ".".join((*self.function_stack, function_name))
        return ReceiverRecord(
            signal=signal,
            sender=_keyword_name(node, "sender"),
            receiver=qualified_name,
            file=str(self.source_file),
            line=node.lineno,
        )


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _keyword_name(call: ast.Call, name: str) -> str | None:
    value = _keyword_value(call, name)
    return _expression_name(value) if value is not None else None


def _expression_name(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expression_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        return _expression_name(node.func)
    return None


def _is_site_packages(path: Path) -> bool:
    return any(part.lower() in {"site-packages", "dist-packages"} for part in path.parts)
