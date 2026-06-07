"""Unit tests for the symbol extraction parsers.

Covers Python (AST), JS/TS (regex), Java (regex), Go (regex),
edge cases, and the PARSERS registry.
"""

from __future__ import annotations

from minimax_code.agent.perception.parsers import (
    PARSERS,
    SymbolNode,
    extract_go_symbols,
    extract_java_symbols,
    extract_js_ts_symbols,
    extract_python_symbols,
)


# ---------------------------------------------------------------------------
# Python parser
# ---------------------------------------------------------------------------


class TestPythonParser:
    def test_extract_classes(self) -> None:
        code = "class Foo:\n    pass\n\nclass Bar:\n    pass\n"
        nodes = extract_python_symbols(code, "test.py")
        names = [n.name for n in nodes]
        assert "Foo" in names
        assert "Bar" in names
        assert all(n.kind == "class" for n in nodes)

    def test_extract_functions(self) -> None:
        code = "def hello():\n    pass\n\ndef world():\n    pass\n"
        nodes = extract_python_symbols(code, "test.py")
        names = [n.name for n in nodes]
        assert "hello" in names
        assert "world" in names
        assert all(n.kind == "function" for n in nodes)

    def test_extract_async_functions(self) -> None:
        code = "async def fetch():\n    pass\n"
        nodes = extract_python_symbols(code, "test.py")
        assert len(nodes) == 1
        assert nodes[0].name == "fetch"
        assert nodes[0].kind == "function"

    def test_extract_nested_methods(self) -> None:
        code = (
            "class Service:\n"
            "    def start(self):\n"
            "        pass\n"
            "\n"
            "    async def stop(self):\n"
            "        pass\n"
        )
        nodes = extract_python_symbols(code, "test.py")
        assert len(nodes) == 1
        cls = nodes[0]
        assert cls.name == "Service"
        assert cls.kind == "class"
        method_names = [m.name for m in cls.children]
        assert "start" in method_names
        assert "stop" in method_names

    def test_handle_syntax_errors_gracefully(self) -> None:
        code = "def broken(\n  this is not valid python\n"
        nodes = extract_python_symbols(code, "bad.py")
        assert nodes == []

    def test_top_level_variables(self) -> None:
        code = "VERSION = '1.0'\nDEBUG = True\n"
        nodes = extract_python_symbols(code, "test.py")
        var_names = [n.name for n in nodes if n.kind == "variable"]
        assert "VERSION" in var_names
        assert "DEBUG" in var_names

    def test_empty_file_returns_empty_list(self) -> None:
        assert extract_python_symbols("", "empty.py") == []

    def test_property_decorator(self) -> None:
        code = (
            "class Model:\n"
            "    @property\n"
            "    def name(self):\n"
            "        return self._name\n"
        )
        nodes = extract_python_symbols(code, "test.py")
        assert len(nodes) == 1
        methods = nodes[0].children
        assert any(m.kind == "property" and m.name == "name" for m in methods)

    def test_staticmethod_decorator(self) -> None:
        code = (
            "class Util:\n"
            "    @staticmethod\n"
            "    def create():\n"
            "        pass\n"
        )
        nodes = extract_python_symbols(code, "test.py")
        methods = nodes[0].children
        assert any(m.kind == "staticmethod" and m.name == "create" for m in methods)

    def test_classmethod_decorator(self) -> None:
        code = (
            "class Factory:\n"
            "    @classmethod\n"
            "    def from_config(cls):\n"
            "        pass\n"
        )
        nodes = extract_python_symbols(code, "test.py")
        methods = nodes[0].children
        assert any(m.kind == "classmethod" and m.name == "from_config" for m in methods)


# ---------------------------------------------------------------------------
# JS/TS parser
# ---------------------------------------------------------------------------


class TestJsTsParser:
    def test_extract_functions(self) -> None:
        code = "function hello() {\n  return 1;\n}\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        assert any(n.name == "hello" and n.kind == "function" for n in nodes)

    def test_extract_classes(self) -> None:
        code = "class App {\n  constructor() {}\n}\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        assert any(n.name == "App" and n.kind == "class" for n in nodes)

    def test_extract_const_let(self) -> None:
        code = "const x = 1;\nlet y = 2;\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        names = [n.name for n in nodes]
        assert "x" in names
        assert "y" in names

    def test_extract_exported_symbols(self) -> None:
        code = "export function main() {}\nexport class Widget {}\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        names = [n.name for n in nodes]
        assert "main" in names
        assert "Widget" in names

    def test_extract_interface(self) -> None:
        code = "interface Config {\n  host: string;\n}\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        assert any(n.name == "Config" and n.kind == "interface" for n in nodes)

    def test_extract_type_alias(self) -> None:
        code = "type ID = string;\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        assert any(n.name == "ID" and n.kind == "type" for n in nodes)

    def test_extract_enum(self) -> None:
        code = "enum Color { Red, Green, Blue }\n"
        nodes = extract_js_ts_symbols(code, "test.ts")
        assert any(n.name == "Color" and n.kind == "enum" for n in nodes)

    def test_empty_file_returns_empty_list(self) -> None:
        assert extract_js_ts_symbols("", "empty.ts") == []


# ---------------------------------------------------------------------------
# Java parser
# ---------------------------------------------------------------------------


class TestJavaParser:
    def test_extract_classes(self) -> None:
        code = (
            "public class HelloWorld {\n"
            "    public static void main(String[] args) {\n"
            "        System.out.println(\"Hello\");\n"
            "    }\n"
            "}\n"
        )
        nodes = extract_java_symbols(code, "HelloWorld.java")
        assert any(n.name == "HelloWorld" and n.kind == "class" for n in nodes)

    def test_extract_methods(self) -> None:
        code = (
            "public class Service {\n"
            "    public void start() {\n"
            "    }\n"
            "\n"
            "    private String getName() {\n"
            "        return \"test\";\n"
            "    }\n"
            "}\n"
        )
        nodes = extract_java_symbols(code, "Service.java")
        method_names = [n.name for n in nodes if n.kind == "method"]
        assert "start" in method_names
        assert "getName" in method_names

    def test_extract_interface(self) -> None:
        code = "public interface Runnable {\n    void run();\n}\n"
        nodes = extract_java_symbols(code, "Runnable.java")
        assert any(n.name == "Runnable" and n.kind == "class" for n in nodes)

    def test_empty_file_returns_empty_list(self) -> None:
        assert extract_java_symbols("", "Empty.java") == []


# ---------------------------------------------------------------------------
# Go parser
# ---------------------------------------------------------------------------


class TestGoParser:
    def test_extract_functions(self) -> None:
        code = "package main\n\nfunc hello() {\n    fmt.Println(\"hello\")\n}\n"
        nodes = extract_go_symbols(code, "main.go")
        assert any(n.name == "hello" and n.kind == "function" for n in nodes)

    def test_extract_struct_types(self) -> None:
        code = (
            "package main\n\n"
            "type Server struct {\n"
            "    Host string\n"
            "    Port int\n"
            "}\n"
        )
        nodes = extract_go_symbols(code, "server.go")
        assert any(n.name == "Server" and n.kind == "type" for n in nodes)

    def test_extract_methods(self) -> None:
        code = (
            "package main\n\n"
            "func (s *Server) Start() error {\n"
            "    return nil\n"
            "}\n"
        )
        nodes = extract_go_symbols(code, "server.go")
        assert any(n.name == "Start" and n.kind == "method" for n in nodes)

    def test_extract_interface_type(self) -> None:
        code = (
            "package main\n\n"
            "type Handler interface {\n"
            "    ServeHTTP()\n"
            "}\n"
        )
        nodes = extract_go_symbols(code, "handler.go")
        assert any(n.name == "Handler" and n.kind == "type" for n in nodes)

    def test_empty_file_returns_empty_list(self) -> None:
        assert extract_go_symbols("", "empty.go") == []


# ---------------------------------------------------------------------------
# PARSERS registry
# ---------------------------------------------------------------------------


class TestParsersRegistry:
    def test_has_python_extension(self) -> None:
        assert ".py" in PARSERS

    def test_has_ts_extensions(self) -> None:
        assert ".ts" in PARSERS
        assert ".tsx" in PARSERS
        assert ".js" in PARSERS
        assert ".jsx" in PARSERS

    def test_has_java_extension(self) -> None:
        assert ".java" in PARSERS

    def test_has_go_extension(self) -> None:
        assert ".go" in PARSERS

    def test_all_parsers_are_callable(self) -> None:
        for ext, parser in PARSERS.items():
            result = parser("", f"test{ext}")
            assert isinstance(result, list)

    def test_symbol_node_fields(self) -> None:
        node = SymbolNode(name="foo", kind="function", line=10)
        assert node.name == "foo"
        assert node.kind == "function"
        assert node.line == 10
        assert node.children == []
