"""Built-in tools for the ``test-generator`` skill.

Tools
-----

* :class:`ExtractFunctionsTool` — parses a Python file with
  :mod:`ast` and returns a list of top-level (and nested) function
  definitions, with their signature, docstring, line range, and
  the source text. The LLM uses this to decide which functions
  need tests and to seed the test bodies with realistic inputs.
* :class:`AnalyzeFunctionDependenciesTool` — given a function
  name and the file it lives in, returns:

  * the names of free (module-level) symbols the function reads
    or calls,
  * the names of *its* parameters and their type annotations,
  * a stripped source snippet,
  * the names of any functions / methods *defined* in the same
    module that the function calls (so the test scaffold can
    monkey-patch them).

The two tools together are enough scaffolding for the LLM to
write a pytest file without re-reading the source line-by-line.
"""

from __future__ import annotations

import ast
from typing import Any

from ...tools.base import Tool, ToolResult
from ...tools.file_ops import PathSecurityError, safe_resolve
from ..runtime import SkillToolProvider


class ExtractFunctionsTool(Tool):
    name = "extract_functions"
    description = (
        "Parse a Python file and return a structured list of every function "
        "and method definition — name, signature, docstring, line range, and "
        "the full source. Use this to seed a pytest file: pick the functions "
        "that need tests, then call `analyze_function_dependencies` for each."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Python source file (must live inside the workspace).",
            },
            "include_private": {
                "type": "boolean",
                "default": False,
                "description": "Include functions whose name starts with an underscore.",
            },
            "max_functions": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5000,
                "default": 500,
                "description": "Cap on the number of functions returned.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        include_private = bool(kwargs.get("include_private", False))
        max_functions = int(kwargs.get("max_functions") or 500)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.is_file():
            return ToolResult.fail(f"not a file: {raw_path}")

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.fail(f"failed to read file: {exc}")

        try:
            tree = ast.parse(text, filename=str(target))
        except SyntaxError as exc:
            return ToolResult.fail(
                f"SyntaxError at line {exc.lineno}: {exc.msg}",
                output={"error": "syntax_error", "line": exc.lineno, "message": exc.msg},
            )

        lines = text.splitlines(keepends=True)
        functions: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not include_private and node.name.startswith("_") and node.name != "__init__":
                # Magic methods (__init__, __repr__, etc.) are still
                # surfaced because they often need tests; everything
                # else private is hidden by default.
                if not (node.name.startswith("__") and node.name.endswith("__")):
                    continue
            if node.end_lineno is None:
                continue
            source = "".join(lines[node.lineno - 1 : node.end_lineno])
            functions.append(
                {
                    "name": node.name,
                    "qualname": _qualname(tree, node),
                    "line": int(node.lineno),
                    "end_line": int(node.end_lineno),
                    "args": _arg_signature(node.args),
                    "returns": _annotation_to_str(node.returns),
                    "decorators": [_expr_to_str(d) for d in node.decorator_list],
                    "docstring": ast.get_docstring(node) or "",
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "source": source,
                }
            )
            if len(functions) >= max_functions:
                break

        return ToolResult.ok(
            output={
                "path": str(target),
                "functions": functions,
                "total": len(functions),
                "truncated": len(functions) >= max_functions,
            },
            count=len(functions),
            truncated=len(functions) >= max_functions,
        )


class AnalyzeFunctionDependenciesTool(Tool):
    name = "analyze_function_dependencies"
    description = (
        "Given a function name and the file it lives in, return the function's "
        "parameters (with type annotations), the names of free symbols it "
        "references (for monkey-patching), the names of other functions it "
        "calls in the same module, and the function's source snippet. Use "
        "this after `extract_functions` to scaffold a test that mocks the "
        "right collaborators."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Python source file (must live inside the workspace).",
            },
            "func_name": {
                "type": "string",
                "description": "Name of the function to analyse. If multiple definitions share a name, the first is returned.",
            },
        },
        "required": ["path", "func_name"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        func_name = (kwargs.get("func_name") or "").strip()
        if not func_name:
            return ToolResult.fail("'func_name' is required")

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.is_file():
            return ToolResult.fail(f"not a file: {raw_path}")

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.fail(f"failed to read file: {exc}")
        lines = text.splitlines(keepends=True)

        try:
            tree = ast.parse(text, filename=str(target))
        except SyntaxError as exc:
            return ToolResult.fail(
                f"SyntaxError at line {exc.lineno}: {exc.msg}",
                output={"error": "syntax_error", "line": exc.lineno, "message": exc.msg},
            )

        func_node = _find_function(tree, func_name)
        if func_node is None:
            return ToolResult.fail(f"function {func_name!r} not found in {raw_path}")

        # Free symbols = names used in the function body that are
        # neither parameters, locals, nor builtins.
        params = _param_names(func_node)
        locals_ = _local_names(func_node)
        free: set[str] = set()
        called: set[str] = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    free.add(node.id)
            elif isinstance(node, ast.Attribute):
                # Record the root name too, so e.g. `self.helper(...)`
                # surfaces `self` as a free symbol.
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and isinstance(root.ctx, ast.Load):
                    free.add(root.id)
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    called.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    # `obj.method(...)` — record the attribute name.
                    called.add(fn.attr)
        free -= params | locals_ | _BUILTINS

        # Functions defined in the same module that this one calls —
        # useful for fixture / monkeypatch planning.
        module_funcs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not func_node:
                module_funcs.add(node.name)

        source = ""
        if func_node.end_lineno is not None and func_node.lineno is not None:
            source = "".join(lines[func_node.lineno - 1 : func_node.end_lineno])

        return ToolResult.ok(
            output={
                "path": str(target),
                "name": func_node.name,
                "qualname": _qualname(tree, func_node),
                "line": int(func_node.lineno or 0),
                "end_line": int(func_node.end_lineno or 0),
                "parameters": [
                    {"name": a.arg, "annotation": _annotation_to_str(a.annotation)}
                    for a in func_node.args.args
                ],
                "returns": _annotation_to_str(func_node.returns),
                "free_symbols": sorted(free),
                "calls": sorted(called),
                "calls_in_module": sorted(called & module_funcs),
                "docstring": ast.get_docstring(func_node) or "",
                "source": source,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# A small subset of builtin names — not exhaustive, but covers
# the ones a function would typically reference. Anything we
# don't know about is treated as free, which is fine: the LLM
# can decide what to monkey-patch.
_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "Exception",
    "False",
    "float",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "None",
    "object",
    "open",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "True",
    "tuple",
    "type",
    "zip",
}


def _qualname(tree: ast.AST, target: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return ``ClassName.method`` if ``target`` is nested in a class."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if child is target:
                    return f"{node.name}.{target.name}"
    return target.name


def _arg_signature(args: ast.arguments) -> str:
    """Render a function's argument list as a string, e.g. ``(a, b=2, *args)``."""
    parts: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    default_offset = len(positional) - len(defaults)
    for i, a in enumerate(positional):
        s = a.arg
        ann = _annotation_to_str(a.annotation)
        if ann:
            s += f": {ann}"
        if i >= default_offset:
            d = defaults[i - default_offset]
            s += f"={ast.unparse(d)}"
        parts.append(s)
    if args.vararg:
        s = f"*{args.vararg.arg}"
        if args.vararg.annotation:
            s += f": {_annotation_to_str(args.vararg.annotation)}"
        parts.append(s)
    elif args.kwonlyargs:
        parts.append("*")
    for i, a in enumerate(args.kwonlyargs):
        s = a.arg
        ann = _annotation_to_str(a.annotation)
        if ann:
            s += f": {ann}"
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            s += f"={ast.unparse(args.kw_defaults[i])}"
        parts.append(s)
    if args.kwarg:
        s = f"**{args.kwarg.arg}"
        if args.kwarg.annotation:
            s += f": {_annotation_to_str(args.kwarg.annotation)}"
        parts.append(s)
    return "(" + ", ".join(parts) + ")"


def _annotation_to_str(ann: ast.AST | None) -> str:
    if ann is None:
        return ""
    try:
        return ast.unparse(ann)
    except Exception:  # pragma: no cover — defensive
        return ""


def _expr_to_str(expr: ast.AST) -> str:
    try:
        return ast.unparse(expr)
    except Exception:  # pragma: no cover
        return ""


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    out: set[str] = set()
    args = fn.args
    for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        out.add(a.arg)
    if args.vararg:
        out.add(args.vararg.arg)
    if args.kwarg:
        out.add(args.kwarg.arg)
    return out


def _local_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names bound inside ``fn`` (assignments, imports, loop vars, with-items)."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                _collect_assigned(tgt, out)
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            _collect_assigned(node.target, out)
        elif isinstance(node, ast.AugAssign):
            _collect_assigned(node.target, out)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _collect_assigned(node.target, out)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _collect_assigned(item.optional_vars, out)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.asname or alias.name)
        elif isinstance(node, ast.FunctionDef) and node is not fn:
            out.add(node.name)
        elif isinstance(node, ast.AsyncFunctionDef) and node is not fn:
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            out.add(node.name)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                _collect_assigned(gen.target, out)
    return out


def _collect_assigned(target: ast.AST, into: set[str]) -> None:
    if isinstance(target, ast.Name):
        into.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_assigned(elt, into)
    elif isinstance(target, ast.Starred):
        _collect_assigned(target.value, into)


def _find_function(
    tree: ast.AST, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """First function in ``tree`` whose simple name matches ``name``."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(SkillToolProvider):
    """Adds the test-generator tools to the agent's tool registry."""

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        added: set[str] = set()
        for cls in (ExtractFunctionsTool, AnalyzeFunctionDependenciesTool):
            if not tool_registry.has(cls.name):
                tool_registry.register(cls())
                added.add(cls.name)
        return added

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        for cls in (ExtractFunctionsTool, AnalyzeFunctionDependenciesTool):
            tool_registry.unregister(cls.name)


__all__ = [
    "AnalyzeFunctionDependenciesTool",
    "ExtractFunctionsTool",
    "Provider",
]
