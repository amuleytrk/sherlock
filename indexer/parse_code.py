"""tree-sitter parser for JavaScript/TypeScript code.

Emits one block per top-level method or function — for object-literal
controllers (Trackonomy's pattern) we walk into pairs whose value is a
function. For TypeScript classes we walk method_definition nodes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser


_JS_LANG = Language(tsjs.language())
_TS_LANG = Language(tsts.language_typescript())
_TSX_LANG = Language(tsts.language_tsx())


@dataclass
class CodeBlock:
    file_path: str
    name: str
    line_start: int
    line_end: int
    content: str


def _slice(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _emit(node: Node, src: bytes, file_path: str, name: str | None) -> CodeBlock | None:
    if not name:
        return None
    return CodeBlock(
        file_path=file_path,
        name=name,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        content=_slice(node, src),
    )


def _named_function_or_method(node: Node, src: bytes, file_path: str) -> CodeBlock | None:
    # function declaration: function foo() {}
    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        return _emit(node, src, file_path, name_node.text.decode() if name_node else None)
    # class method
    if node.type == "method_definition":
        name_node = node.child_by_field_name("name")
        return _emit(node, src, file_path, name_node.text.decode() if name_node else None)
    # object pair with function value: { foo() {}, async foo() {} }
    if node.type == "pair":
        key = node.child_by_field_name("key")
        value = node.child_by_field_name("value")
        if value and key and value.type in {"function_expression", "arrow_function"}:
            return _emit(node, src, file_path, key.text.decode())
    # const foo = () => {}
    if node.type == "variable_declarator":
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if (
            value
            and name_node
            and value.type in {"arrow_function", "function_expression"}
        ):
            return _emit(node, src, file_path, name_node.text.decode())
    return None


def _walk_for_blocks(root: Node, src: bytes, file_path: str) -> list[CodeBlock]:
    blocks: list[CodeBlock] = []

    def descend(n: Node) -> None:
        emit = _named_function_or_method(n, src, file_path)
        if emit:
            blocks.append(emit)
            return  # don't dive into nested fns; we want top-level methods
        for child in n.children:
            descend(child)

    descend(root)
    return blocks


def parse_js(text: str, file_path: str) -> list[CodeBlock]:
    parser = Parser(_JS_LANG)
    src = text.encode("utf-8")
    tree = parser.parse(src)
    return _walk_for_blocks(tree.root_node, src, file_path)


def parse_ts(text: str, file_path: str) -> list[CodeBlock]:
    is_tsx = file_path.endswith(".tsx") or file_path.endswith(".jsx")
    parser = Parser(_TSX_LANG if is_tsx else _TS_LANG)
    src = text.encode("utf-8")
    tree = parser.parse(src)
    return _walk_for_blocks(tree.root_node, src, file_path)


def parse_code_file(path: Path) -> list[CodeBlock]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".js":
        return parse_js(text, str(path))
    if path.suffix == ".jsx":
        return parse_js(text, str(path))
    if path.suffix in {".ts", ".tsx"}:
        return parse_ts(text, str(path))
    return []
