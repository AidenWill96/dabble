#!/usr/bin/env python3
"""dabble 0.4.1: a native dabble-to-browser-JavaScript compiler.

Requires Python 3.10+. No third-party packages.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import subprocess
import time
from urllib.request import urlopen
from importlib.resources import files as package_files
import dabble_syntax as syntax
import json
import math
import re
import shutil
import signal
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import sys


VERSION = "0.5.0"


TAGS = {
    "a", "article", "aside", "b", "br", "button", "code", "div", "em",
    "footer", "form", "h1", "h2", "h3", "header", "img", "label", "li",
    "main", "nav", "ol", "option", "p", "pre", "section", "select",
    "small", "span", "strong", "textarea", "ul",
}
BUILTINS = TAGS | {"input_", "el", "mount", "signal", "computed", "effect", "range", "len", "str", "print", "fetch_json", "post_json", "storage_get", "storage_set", "navigate", "path", "on_route", "route"}
JS_RESERVED = {
    "arguments", "await", "break", "case", "catch", "class", "const", "continue",
    "debugger", "default", "delete", "do", "else", "enum", "eval", "export",
    "extends", "false", "finally", "for", "function", "if", "import", "in",
    "instanceof", "let", "new", "null", "return", "static", "super", "switch",
    "this", "throw", "true", "try", "typeof", "var", "void", "while", "with",
    "yield", "implements", "interface", "package", "private", "protected",
    "public", "__dabble", "__modules",
}


class DabbleError(Exception):
    """An unsupported or invalid dabble source construct."""


class Compiler:
    def __init__(self, filename: str, *, target="browser", import_ids=None, styled_components=None):
        self.filename = filename
        self.target = target
        self.import_ids = import_ids or {}
        self.styled_components = styled_components or set()
        self.component_name = None
        self.scopes: list[set[str]] = []
        self.function_depth = 0
        self.loop_depth = 0
        self.async_depth = 0

    def fail(self, node: syntax.AST, reason: str):
        line = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0) + 1
        raise DabbleError(f"{self.filename}:{line}:{col}: {reason}")

    def name(self, node: syntax.AST, value: str) -> str:
        if not value.isascii() or not value.isidentifier() or value in JS_RESERVED:
            self.fail(node, f"{value!r} cannot be used as a dabble identifier")
        return value

    def declarations(self, statements: list[syntax.stmt]) -> tuple[set[str], set[str]]:
        """Find local assignments, excluding nested function bodies."""
        assigned: set[str] = set()
        functions: set[str] = set()

        def visit(stmt: syntax.stmt):
            if isinstance(stmt, (syntax.Import, syntax.ImportFrom)):
                if isinstance(stmt, syntax.Import): functions.add(self.name(stmt, stmt.alias))
                else:
                    for original, alias in stmt.names: functions.add(self.name(stmt, alias))
                return
            if isinstance(stmt, (syntax.FunctionDef, syntax.AsyncFunctionDef)):
                functions.add(self.name(stmt, stmt.name))
                return
            if isinstance(stmt, syntax.Assign):
                if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], syntax.Name):
                    self.fail(stmt, "assignment requires one plain variable name")
                assigned.add(self.name(stmt.targets[0], stmt.targets[0].id))
            elif isinstance(stmt, syntax.AugAssign):
                if not isinstance(stmt.target, syntax.Name):
                    self.fail(stmt, "augmented assignment requires a variable name")
                assigned.add(self.name(stmt.target, stmt.target.id))
            elif isinstance(stmt, syntax.For):
                if not isinstance(stmt.target, syntax.Name):
                    self.fail(stmt, "for loops require a single variable name")
                assigned.add(self.name(stmt.target, stmt.target.id))
                for s in stmt.body:
                    visit(s)
                if stmt.orelse:
                    self.fail(stmt, "for/else is not yet supported")
            elif isinstance(stmt, (syntax.If, syntax.While)):
                for s in stmt.body + stmt.orelse:
                    visit(s)
            elif isinstance(stmt, syntax.Try):
                if stmt.orelse:
                    self.fail(stmt, "try/else is not supported")
                for part in [stmt.body, stmt.finalbody, *(h.body for h in stmt.handlers)]:
                    for s in part:
                        visit(s)
            return

        for statement in statements:
            visit(statement)
        if assigned & functions:
            self.fail(statements[0], "a variable and function cannot share the same name in one scope")
        return assigned, functions

    def body(self, statements: list[syntax.stmt], indent: int, params: tuple[str, ...] = ()) -> list[str]:
        assigned, functions = self.declarations(statements)
        if assigned & set(params) or functions & set(params):
            self.fail(statements[0], "parameters cannot be redeclared in the same function")
        self.scopes.append(assigned | functions | set(params))
        pad = "    " * indent
        result: list[str] = []
        if assigned:
            result.append(pad + "let " + ", ".join(sorted(assigned)) + ";")
        for statement in statements:
            result.extend(self.stmt(statement, indent))
        self.scopes.pop()
        return result

    def block(self, statements: list[syntax.stmt], indent: int) -> str:
        lines = self.statements_no_scope(statements, indent + 1)
        return "{\n" + "\n".join(lines) + "\n" + "    " * indent + "}"

    def statements_no_scope(self, statements: list[syntax.stmt], indent: int) -> list[str]:
        result: list[str] = []
        for statement in statements:
            result.extend(self.stmt(statement, indent))
        return result or ["    " * indent + ";"]

    def stmt(self, node: syntax.stmt, indent: int) -> list[str]:
        pad = "    " * indent
        if isinstance(node, (syntax.FunctionDef, syntax.AsyncFunctionDef)):
            if node.decorator_list or node.returns or getattr(node, "type_params", []):
                self.fail(node, "decorators, return annotations and type parameters are not supported")
            a = node.args
            if a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults or any(a.kw_defaults):
                self.fail(node, "only simple positional function parameters are supported")
            if any(x.annotation for x in a.args):
                self.fail(node, "parameter annotations are not supported")
            params = tuple(self.name(x, x.arg) for x in a.args)
            if len(params) != len(set(params)):
                self.fail(node, "duplicate function parameter")
            old_loop_depth = self.loop_depth
            old_async_depth = self.async_depth
            self.loop_depth = 0
            self.async_depth = 1 if isinstance(node, syntax.AsyncFunctionDef) else 0
            old_component = self.component_name
            self.component_name = node.name if isinstance(node, syntax.ComponentDef) and node.name in self.styled_components else None
            self.function_depth += 1
            try:
                lines = self.body(node.body, indent + 1, params)
            finally:
                self.function_depth -= 1
                self.component_name = old_component
                self.loop_depth = old_loop_depth
                self.async_depth = old_async_depth
            prefix = "async function" if isinstance(node, syntax.AsyncFunctionDef) else "function"
            return [pad + f"{prefix} {self.name(node, node.name)}({', '.join(params)}) {{", *lines, pad + "}"]
        if isinstance(node, syntax.StyleDef):
            if self.function_depth:
                self.fail(node, "style blocks must be at module scope")
            return []
        if isinstance(node, syntax.Import):
            if self.function_depth: self.fail(node, "imports must be at module scope")
            if node.module not in self.import_ids: self.fail(node, "unresolved module import; use dabble compile with a local .dab module")
            return [pad + f"const {self.name(node, node.alias)} = __modules[{json.dumps(self.import_ids[node.module])}];"]
        if isinstance(node, syntax.ImportFrom):
            if self.function_depth: self.fail(node, "imports must be at module scope")
            if node.module not in self.import_ids: self.fail(node, "unresolved module import; use dabble compile with a local .dab module")
            mid = json.dumps(self.import_ids[node.module])
            return [pad + f"const {self.name(node, alias)} = __modules[{mid}][{json.dumps(original)}];" for original, alias in node.names]
        if isinstance(node, syntax.State):
            # Native syntax always creates a dabble signal, even if a local
            # variable or parameter happens to be named "signal".
            return [pad + f"{node.targets[0].id} = __dabble.signal({self.expr(node.initial)});"]
        if isinstance(node, syntax.Assign):
            # Target shape was checked while calculating local declarations.
            return [pad + f"{node.targets[0].id} = {self.expr(node.value)};"]
        if isinstance(node, syntax.AugAssign):
            ops = {syntax.Add: "+", syntax.Sub: "-", syntax.Mult: "*", syntax.Div: "/", syntax.Mod: "%"}
            op = ops.get(type(node.op))
            if op is None:
                self.fail(node, "unsupported augmented assignment operator")
            return [pad + f"{node.target.id} {op}= {self.expr(node.value)};"]
        if isinstance(node, syntax.Return):
            if self.function_depth == 0:
                self.fail(node, "return is only valid inside functions")
            value = self.expr(node.value) if node.value else None
            if value is not None and self.component_name and self.function_depth == 1:
                value = f"__dabble.scope({value}, {json.dumps('dabble-' + self.component_name)})"
            return [pad + "return" + (" " + value if value is not None else "") + ";"]
        if isinstance(node, syntax.Expr):
            return [pad + self.expr(node.value) + ";"]
        if isinstance(node, syntax.If):
            result = [pad + f"if (__dabble.truthy({self.expr(node.test)})) " + self.block(node.body, indent)]
            if node.orelse:
                result[-1] += " else " + self.block(node.orelse, indent)
            return result
        if isinstance(node, syntax.While):
            if node.orelse:
                self.fail(node, "while/else is not supported")
            test = self.expr(node.test)
            self.loop_depth += 1
            try:
                block = self.block(node.body, indent)
            finally:
                self.loop_depth -= 1
            return [pad + f"while (__dabble.truthy({test})) " + block]
        if isinstance(node, syntax.For):
            iterable = self.expr(node.iter)
            self.loop_depth += 1
            try:
                block = self.block(node.body, indent)
            finally:
                self.loop_depth -= 1
            return [pad + f"for ({node.target.id} of {iterable}) " + block]
        if isinstance(node, syntax.Break):
            if self.loop_depth == 0:
                self.fail(node, "break is only valid inside loops")
            return [pad + "break;"]
        if isinstance(node, syntax.Continue):
            if self.loop_depth == 0:
                self.fail(node, "continue is only valid inside loops")
            return [pad + "continue;"]
        if isinstance(node, syntax.Try):
            if node.orelse:
                self.fail(node, "try/else is not supported")
            if len(node.handlers) > 1:
                self.fail(node, "try currently supports one except clause")
            if not node.handlers and not node.finalbody:
                self.fail(node, "try requires except or finally")
            result = pad + "try " + self.block(node.body, indent)
            if node.handlers:
                handler = node.handlers[0]
                if handler.type is not None and not (isinstance(handler.type, syntax.Name) and handler.type.id == "Exception"):
                    self.fail(handler, "only bare except or except Exception is supported")
                if handler.name:
                    # A JS catch binding is local to the catch clause.
                    identifier = self.name(handler, handler.name)
                    self.scopes.append({identifier})
                else:
                    identifier = "__error"
                    self.scopes.append(set())
                try:
                    catch_body = self.block(handler.body, indent)
                finally:
                    self.scopes.pop()
                result += f" catch ({identifier}) " + catch_body
            if node.finalbody:
                result += " finally " + self.block(node.finalbody, indent)
            return [result]
        if isinstance(node, syntax.Raise):
            if node.exc is None or node.cause is not None:
                self.fail(node, "raise requires a value; raise/from are unsupported")
            return [pad + "throw " + self.expr(node.exc) + ";"]
        if isinstance(node, syntax.Pass):
            return [pad + ";"]
        self.fail(node, f"unsupported statement: {type(node).__name__}")

    def expr(self, node: syntax.AST) -> str:
        if isinstance(node, syntax.Await):
            if self.async_depth == 0:
                self.fail(node, "await is only valid inside async functions")
            return "(await " + self.expr(node.value) + ")"
        if isinstance(node, syntax.Constant):
            if node.value is None:
                return "null"
            if isinstance(node.value, (str, bool, int)):
                return json.dumps(node.value, ensure_ascii=False)
            if isinstance(node.value, float) and math.isfinite(node.value):
                return repr(node.value)
            self.fail(node, "unsupported literal")
        if isinstance(node, syntax.Name):
            name = self.name(node, node.id)
            if name in BUILTINS and not any(name in scope for scope in self.scopes):
                return f"__dabble.{name}"
            return name
        if isinstance(node, syntax.Call):
            if any(kw.arg is None for kw in node.keywords):
                self.fail(node, "**keyword expansion is not supported")
            if node.keywords and not (isinstance(node.func, syntax.Name) and node.func.id in TAGS | {"input_", "el"} and not any(node.func.id in scope for scope in self.scopes)):
                self.fail(node, "keyword properties are only supported on HTML element builders")
            args = [self.expr(a) for a in node.args]
            if node.keywords:
                props = ", ".join(f"{json.dumps(kw.arg)}: {self.expr(kw.value)}" for kw in node.keywords)
                args.append("{" + props + "}")
            return f"{self.expr(node.func)}({', '.join(args)})"
        if isinstance(node, syntax.Attribute):
            return f"{self.expr(node.value)}.{self.name(node, node.attr)}"
        if isinstance(node, syntax.BinOp):
            ops = {syntax.Add: "+", syntax.Sub: "-", syntax.Mult: "*", syntax.Div: "/", syntax.Mod: "%", syntax.Pow: "**"}
            if isinstance(node.op, syntax.FloorDiv):
                return f"Math.floor({self.expr(node.left)} / {self.expr(node.right)})"
            op = ops.get(type(node.op))
            if not op:
                self.fail(node, "unsupported binary operator")
            return f"({self.expr(node.left)} {op} {self.expr(node.right)})"
        if isinstance(node, syntax.UnaryOp):
            if isinstance(node.op, syntax.Not):
                return f"(!__dabble.truthy({self.expr(node.operand)}))"
            if isinstance(node.op, syntax.USub):
                return f"(-{self.expr(node.operand)})"
            if isinstance(node.op, syntax.UAdd):
                return f"(+{self.expr(node.operand)})"
            self.fail(node, "unsupported unary operator")
        if isinstance(node, syntax.BoolOp):
            op = " && " if isinstance(node.op, syntax.And) else " || "
            return "(" + op.join(f"__dabble.truthy({self.expr(v)})" for v in node.values) + ")"
        if isinstance(node, syntax.Compare):
            if len(node.ops) != 1:
                self.fail(node, "chained comparisons are not yet supported")
            ops = {syntax.Eq: "===", syntax.NotEq: "!==", syntax.Lt: "<", syntax.LtE: "<=", syntax.Gt: ">", syntax.GtE: ">="}
            op = ops.get(type(node.ops[0]))
            if not op:
                self.fail(node, "unsupported comparison operator")
            return f"({self.expr(node.left)} {op} {self.expr(node.comparators[0])})"
        if isinstance(node, syntax.IfExp):
            return f"(__dabble.truthy({self.expr(node.test)}) ? {self.expr(node.body)} : {self.expr(node.orelse)})"
        if isinstance(node, (syntax.List, syntax.Tuple)):
            return "[" + ", ".join(self.expr(e) for e in node.elts) + "]"
        if isinstance(node, syntax.Dict):
            if any(key is None for key in node.keys):
                self.fail(node, "**dictionary expansion is not supported")
            return "{" + ", ".join(f"[{self.expr(k)}]: {self.expr(v)}" for k, v in zip(node.keys, node.values)) + "}"
        if isinstance(node, syntax.Subscript):
            return f"{self.expr(node.value)}[{self.expr(node.slice)}]"
        if isinstance(node, syntax.Lambda):
            a = node.args
            if a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults or any(a.kw_defaults):
                self.fail(node, "lambda only supports simple positional parameters")
            params = [self.name(x, x.arg) for x in a.args]
            self.scopes.append(set(params))
            old_async = self.async_depth
            self.async_depth = 0
            try:
                body = self.expr(node.body)
            finally:
                self.async_depth = old_async
                self.scopes.pop()
            return f"(({', '.join(params)}) => {body})"
        if isinstance(node, syntax.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, syntax.Constant) and isinstance(value.value, str):
                    s = value.value.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                    parts.append(s)
                elif isinstance(value, syntax.FormattedValue):
                    if value.conversion not in (-1, 115) or value.format_spec:
                        self.fail(value, "f-strings do not support format specifiers or !r/!a")
                    parts.append("${" + self.expr(value.value) + "}")
                else:
                    self.fail(value, "unsupported f-string part")
            return "`" + "".join(parts) + "`"
        self.fail(node, f"unsupported expression: {type(node).__name__}")

    def compile_module(self, tree: syntax.Module, module_id: str) -> str:
        self.styled_components = {n.name for n in tree.body if isinstance(n, syntax.StyleDef)}
        statements = self.body(tree.body, 2)
        exports = []
        for statement in tree.body:
            if isinstance(statement, (syntax.FunctionDef, syntax.AsyncFunctionDef)):
                exports.append(statement.name)
            elif isinstance(statement, (syntax.Assign, syntax.State)) and isinstance(statement.targets[0], syntax.Name):
                exports.append(statement.targets[0].id)
        exports = list(dict.fromkeys(n for n in exports if not n.startswith("_")))
        return (f"    __modules[{json.dumps(module_id)}] = (() => {{\n"
                + "\n".join(statements) + "\n"
                + "        return {" + ", ".join(f"{json.dumps(n)}: {n}" for n in exports) + "};\n"
                + "    })();\n")

    def compile(self, source: str) -> str:
        try:
            tree = syntax.parse(source, filename=self.filename)
        except SyntaxError as exc:
            raise DabbleError(f"{self.filename}:{exc.lineno}:{exc.offset}: {exc.msg}") from None
        self.styled_components = {n.name for n in tree.body if isinstance(n, syntax.StyleDef)}
        lines = self.body(tree.body, 1)
        return (
            "// Generated by dabble 0.5 (native frontend). Do not edit directly.\n"
            '(() => {\n    "use strict";\n'
            '    const __dabble = window.dabble;\n'
            '    if (!__dabble) throw new Error("dabble runtime.js must load before compiled code");\n'
            + "\n".join(lines)
            + "\n})();\n"
        )


STYLE_PROP = re.compile(r"^[a-z][a-z0-9_]*$")
CSS_VALUE_BLOCK = re.compile(r"[{};\x00-\x1f]")


def emit_css(tree: syntax.Module, filename: str) -> str:
    """CSS is declarative and statically compiled, scoped to component roots."""
    out = ["/* Generated by dabble 0.5. Do not edit. */"]
    for node in tree.body:
        if not isinstance(node, syntax.StyleDef): continue
        if not node.name.isascii() or not node.name.isidentifier():
            raise DabbleError(f"{filename}:{node.lineno}: invalid styled component name")
        for pseudo, props in node.rules.items():
            if not props: continue
            selector = f".dabble-{node.name}" + (f":{pseudo}" if pseudo != "base" else "")
            out.append(selector + " {")
            for key, value in props.items():
                if not STYLE_PROP.fullmatch(key):
                    raise DabbleError(f"{filename}:{node.lineno}: invalid CSS property {key!r}")
                value = str(value)
                if CSS_VALUE_BLOCK.search(value) or "</" in value:
                    raise DabbleError(f"{filename}:{node.lineno}: unsafe CSS value for {key}")
                out.append(f"  {key.replace('_', '-')}: {value};")
            out.append("}")
    return "\n".join(out) + "\n"


class ModuleGraph:
    """Deterministic local dependency graph, cycles and missing exports rejected."""
    def __init__(self, project_root: Path, target: str):
        self.root = project_root.resolve()
        self.target = target
        self.entries: dict[str, tuple[Path, syntax.Module]] = {}
        self.ordered: list[str] = []
        self.visiting: set[str] = set()
        self.css: list[str] = []

    def resolve(self, importer: Path, module: str) -> Path:
        name = module.replace(".", "/")
        for folder in (self.root, self.root / "dabble_packages"):
            candidate = (folder / (name + ".dab")).resolve()
            if candidate.is_relative_to(folder.resolve()) and candidate.is_file():
                return candidate
        raise DabbleError(f"{importer}: cannot resolve module {module!r} (looked in project and dabble_packages)")

    def visit(self, file: Path):
        file = file.resolve()
        key = str(file)
        if key in self.visiting:
            raise DabbleError(f"{file}: circular module import")
        if key in self.entries: return
        if not file.is_file(): raise DabbleError(f"source not found: {file}")
        source = file.read_text(encoding="utf-8")
        try: tree = syntax.parse(source, filename=str(file))
        except SyntaxError as exc:
            raise DabbleError(pretty_error(DabbleError(f"{file}:{exc.lineno}:{exc.offset}: {exc.msg}"), source)) from None
        self.visiting.add(key)
        self.entries[key] = (file, tree)
        for stmt in tree.body:
            if isinstance(stmt, (syntax.Import, syntax.ImportFrom)):
                try: dependency = self.resolve(file, stmt.module)
                except DabbleError as exc: raise DabbleError(pretty_error(DabbleError(f"{file}:{stmt.lineno}:{stmt.col_offset + 1}: {exc}"), source)) from None
                self.visit(dependency)
                if isinstance(stmt, syntax.ImportFrom):
                    _, dependency_tree = self.entries[str(dependency)]
                    available = {n.name for n in dependency_tree.body if isinstance(n, (syntax.FunctionDef, syntax.AsyncFunctionDef))}
                    available.update(n.targets[0].id for n in dependency_tree.body
                                     if isinstance(n, (syntax.Assign, syntax.State)) and isinstance(n.targets[0], syntax.Name))
                    for original, alias in stmt.names:
                        if original not in available or original.startswith("_"):
                            raise DabbleError(f"{file}:{stmt.lineno}: {original!r} is not exported by {stmt.module!r}")
        self.visiting.remove(key)
        self.ordered.append(key)

    def compile(self, source_file: Path) -> tuple[str, str]:
        self.visit(source_file)
        entry = str(source_file.resolve())
        lines = ["// dabble 0.5 compiled module bundle", "(() => {", '    "use strict";']
        runtime = 'require("./dabble_server.cjs")' if self.target == "server" else 'window.dabble'
        lines += [f"    const __dabble = {runtime};", '    if (!__dabble) throw new Error("dabble runtime not loaded");',
                  '    const __modules = Object.create(null);']
        for key in self.ordered:
            file, tree = self.entries[key]
            import_ids = {s.module: str(self.resolve(file, s.module)) for s in tree.body if isinstance(s, (syntax.Import, syntax.ImportFrom))}
            compiler = Compiler(str(file), target=self.target, import_ids=import_ids)
            try: lines.append(compiler.compile_module(tree, key))
            except DabbleError as exc:
                raise DabbleError(pretty_error(exc, file.read_text(encoding="utf-8"))) from None
            if self.target == "browser": self.css.append(emit_css(tree, str(file)))
        if self.target == "server":
            lines.append('    __dabble.listen({root: __dirname, port: Number(process.env.DABBLE_PORT || 8000)});')
        lines.append("})();")
        return "\n".join(lines) + "\n", "\n".join(self.css)


def pretty_error(exc: DabbleError, source: str) -> str:
    """Diagnostic includes the originating source line and a column caret."""
    first = str(exc)
    match = re.search(r":([0-9]+):([0-9]+):", first)
    if not match:
        return first
    number, column = int(match[1]), int(match[2])
    lines = source.splitlines()
    if not (1 <= number <= len(lines)):
        return first
    line = lines[number - 1]
    return f"{first}\n    {line}\n    {' ' * (column - 1)}^"


def compile_file(source_file: Path, output: Path | None = None, *, root: Path | None = None) -> Path:
    target = output or source_file.with_suffix(".js")
    if source_file.suffix != ".dab":
        raise DabbleError("source filename must end in .dab")
    is_server = source_file.name == "server.dab"
    js, css = ModuleGraph(root or source_file.parent, "server" if is_server else "browser").compile(source_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(js, encoding="utf-8")
    if not is_server:
        target.with_suffix(".css").write_text(css, encoding="utf-8")
    return target


def build_directory(root: Path) -> list[Path]:
    if not root.is_dir():
        raise DabbleError(f"project directory not found: {root}")
    files = sorted(p for p in root.rglob("*.dab") if not any(part.startswith(".") for part in p.relative_to(root).parts))
    if not files:
        raise DabbleError(f"no .dab source files found in {root}")
    return [compile_file(p, root=root) for p in files if "dabble_packages" not in p.relative_to(root).parts]


DEFAULT_STYLES = """/* dabble's starter CSS. Replace with your project's own design. */
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
* { box-sizing: border-box; }
body { max-width: 42rem; margin: 4rem auto; padding: 0 1rem; line-height: 1.55; }
button { font: inherit; padding: .65rem 1rem; cursor: pointer; border-radius: .5rem; }
"""

DEFAULT_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>My dabble app</title>
  <link rel="stylesheet" href="styles.css">
  <link rel="stylesheet" href="app.css">
</head>
<body>
  <div id="app"></div>
  <script src="runtime.js"></script>
  <script src="app.js"></script>
</body>
</html>
"""

# Only migrate the literal HTML produced by dabble 0.4; never alter
# hand-authored HTML, which may intentionally load different CSS assets.
V04_INLINE_STYLE = '<style>body{font:18px system-ui;max-width:36rem;margin:4rem auto;padding:0 1rem}button{padding:.6rem 1rem;cursor:pointer}</style>'


def ensure_project_assets(path: Path) -> list[str]:
    """Create missing web entry assets without clobbering user files.

    A .dab file emits JavaScript, not CSS; this creates starter CSS, not a
    translation of custom styles that aren't present in the source.
    """
    created = []
    runtime = path / "runtime.js"
    if not runtime.exists():
        shutil.copyfile(package_files("dabble_assets").joinpath("runtime.js"), runtime)
        created.append("runtime.js")
    css = path / "styles.css"
    if not css.exists():
        css.write_text(DEFAULT_STYLES, encoding="utf-8")
        created.append("styles.css")
    html = path / "index.html"
    if not html.exists():
        html.write_text(DEFAULT_INDEX, encoding="utf-8")
        created.append("index.html")
    else:
        existing = html.read_text(encoding="utf-8")
        if V04_INLINE_STYLE in existing:
            html.write_text(existing.replace(
                V04_INLINE_STYLE, '<link rel="stylesheet" href="styles.css">'
            ), encoding="utf-8")
            created.append("index.html (upgraded default v0.4 template)")
    # Only rewrite hand-authored HTML to link compiler-generated styles if a
    # native dabble style block actually produced rules.
    if (path / "app.css").is_file() and ".dabble-" in (path / "app.css").read_text(encoding="utf-8"):
        existing = html.read_text(encoding="utf-8")
        if 'href="app.css"' not in existing and "</head>" in existing:
            html.write_text(existing.replace("</head>", '  <link rel="stylesheet" href="app.css">\n</head>', 1), encoding="utf-8")
            created.append("index.html (linked native dabble styles)")
    if (path / "server.dab").exists():
        node_runtime = path / "dabble_server.cjs"
        if not node_runtime.exists():
            shutil.copyfile(package_files("dabble_assets").joinpath("dabble_server.cjs"), node_runtime)
            created.append("dabble_server.cjs")
    return created


def new_project(path: Path, *, fullstack: bool = False) -> None:
    if path.exists() and any(path.iterdir()):
        raise DabbleError(f"directory not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    (path / "app.dab").write_text('''# Your first dabble 0.4.1 app

component App():
    state count = 0
    doubled = computed(lambda: count.get() * 2)

    def increment():
        count.set(count.get() + 1)

    return main(
        h1("Hello from dabble"),
        p("Count: ", count, " · doubled: ", doubled),
        button("Increment", onclick=increment),
    )

mount("#app", App)
''', encoding="utf-8")
    (path / "dabble.json").write_text(json.dumps({"name": path.name, "version":"0.1.0", "dependencies":{}}, indent=2) + "\n")
    if fullstack:
        (path / "server.dab").write_text('''# API endpoints run on the Node.js backend.

def hello(req):
    return {"message": "Hello from the dabble backend!"}

route("GET", "/api/hello", hello)
''', encoding="utf-8")
    ensure_project_assets(path)
    build_directory(path)


def serve_project(directory: Path, port: int, *, dev: bool) -> None:
    """Build a project, then serve either static output or on-demand recompiles."""
    root = directory.resolve()
    files = build_directory(root)
    created = ensure_project_assets(root)
    if created:
        print(f"Created project assets: {', '.join(created)}", flush=True)
    print(f"Compiled {len(files)} dabble file(s)", flush=True)
    if (root / "server.dab").exists():
        run_fullstack(root, port, dev=dev)
        return

    class ProjectServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    class ProjectHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            # In dev mode, avoid serving stale output if compilation fails.
            if dev:
                try:
                    build_directory(root)
                except (DabbleError, OSError) as exc:
                    print(f"dabble: {exc}", file=sys.stderr, flush=True)
                    self.send_error(500, "dabble compilation failed", str(exc))
                    return
            # Serve index.html for client-side routes requested as HTML.
            uri = urlsplit(self.path).path
            if (not (root / uri.lstrip("/")).exists()
                    and "text/html" in self.headers.get("Accept", "")):
                self.path = "/index.html"
            return super().do_GET()

    handler = partial(ProjectHandler, directory=str(root))
    with ProjectServer(("127.0.0.1", port), handler) as server:
        mode = "dev (rebuild on request)" if dev else "run (build once)"
        print(f"dabble {mode}: http://127.0.0.1:{server.server_port}/ (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


PACKAGE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def read_manifest(root: Path) -> dict:
    path = root / "dabble.json"
    if not path.is_file():
        return {"name": root.name, "version": "0.1.0", "dependencies": {}}
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc: raise DabbleError(f"{path}: invalid JSON: {exc}") from None
    if not isinstance(value, dict) or not isinstance(value.get("dependencies", {}), dict):
        raise DabbleError("invalid dabble.json manifest")
    return value


def read_registry(registry: str | None) -> dict:
    if registry is None:
        content = package_files("dabble_assets").joinpath("registry.json").read_text(encoding="utf-8")
    elif registry.startswith("https://"):
        try:
            with urlopen(registry, timeout=10) as response:
                content = response.read(2 * 1024 * 1024 + 1).decode("utf-8")
        except Exception as exc:
            raise DabbleError(f"cannot load registry: {exc}") from None
    elif "://" in registry:
        raise DabbleError("registry must be a local JSON file or HTTPS URL")
    else:
        content = Path(registry).read_text(encoding="utf-8")
    if len(content.encode("utf-8")) > 2 * 1024 * 1024: raise DabbleError("registry size exceeds 2 MiB")
    try: data = json.loads(content)
    except json.JSONDecodeError: raise DabbleError("registry is not valid JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
        raise DabbleError("registry must contain a packages object")
    return data["packages"]


def add_package(root: Path, name: str, registry: str | None = None) -> None:
    if not PACKAGE_ID.fullmatch(name): raise DabbleError("package names must be lowercase alphanumeric or underscore")
    packages = read_registry(registry)
    if name not in packages: raise DabbleError(f"package {name!r} not found in registry")
    package = packages[name]
    if not isinstance(package, dict) or not isinstance(package.get("source"), str) or not isinstance(package.get("sha256"), str):
        raise DabbleError("invalid package manifest")
    raw = package["source"].encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    if sha != package["sha256"]: raise DabbleError(f"sha256 mismatch for package {name!r}; refusing to install")
    if len(raw) > 500_000: raise DabbleError("package exceeds 500 KiB limit")
    try: syntax.parse(package["source"], f"{name}.dab")
    except SyntaxError as exc: raise DabbleError(f"package {name!r} has invalid dabble syntax: {exc.msg}") from None
    folder = root / "dabble_packages"
    file = folder / f"{name}.dab"
    if file.exists() and file.read_bytes() != raw:
        raise DabbleError(f"{file} has local changes; won't overwrite it")
    folder.mkdir(parents=True, exist_ok=True)
    file.write_bytes(raw)
    manifest = read_manifest(root)
    manifest.setdefault("dependencies", {})[name] = package.get("version", "0.0.0")
    (root / "dabble.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lock = root / "dabble.lock"
    data = json.loads(lock.read_text(encoding="utf-8")) if lock.is_file() else {"lockVersion": 1, "packages": {}}
    data["packages"][name] = {"version": package.get("version", "0.0.0"), "sha256": sha}
    lock.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Added {name}@{package.get('version', '0.0.0')} -> dabble_packages/{name}.dab")


def remove_package(root: Path, name: str) -> None:
    manifest = read_manifest(root)
    if name not in manifest.get("dependencies", {}): raise DabbleError(f"package {name!r} is not installed")
    lock_path = root / "dabble.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {"lockVersion":1,"packages":{}}
    file = root / "dabble_packages" / (name + ".dab")
    expected = lock.get("packages", {}).get(name, {}).get("sha256")
    if file.exists() and (expected is None or hashlib.sha256(file.read_bytes()).hexdigest() != expected):
        raise DabbleError(f"{file} was modified; not removing user changes")
    file.unlink(missing_ok=True)
    manifest["dependencies"].pop(name)
    lock.get("packages", {}).pop(name, None)
    (root / "dabble.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(f"Removed {name}")


def run_fullstack(root: Path, port: int, *, dev: bool) -> None:
    if not shutil.which("node"):
        raise DabbleError("full-stack projects require Node.js 18+ (node command not found)")
    env = os.environ.copy()
    env["DABBLE_PORT"] = str(port)

    def spawn():
        return subprocess.Popen(["node", str(root / "server.js")], cwd=root, env=env)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    process = spawn()
    if not dev:
        try: process.wait()
        except KeyboardInterrupt: pass
        finally:
            if process.poll() is None: process.terminate(); process.wait()
            signal.signal(signal.SIGTERM, previous_sigterm)
        return
    timestamps = {str(f): f.stat().st_mtime_ns for f in root.rglob("*.dab") if "dabble_packages" not in f.parts}
    try:
        while process.poll() is None:
            time.sleep(0.35)
            updated = {str(f): f.stat().st_mtime_ns for f in root.rglob("*.dab") if "dabble_packages" not in f.parts}
            if updated == timestamps: continue
            timestamps = updated
            try:
                build_directory(root)
                ensure_project_assets(root)
                print("dabble dev: recompiled changed source", flush=True)
            except (DabbleError, OSError) as exc:
                print(f"dabble dev: compiler error: {exc}", file=sys.stderr, flush=True)
                continue
            process.terminate()
            process.wait(timeout=5)
            process = spawn()
    except KeyboardInterrupt: pass
    finally:
        if process.poll() is None: process.terminate(); process.wait()


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="dabble native browser language compiler")
    cli.add_argument("--version", action="version", version=f"dabble {VERSION}")
    sub = cli.add_subparsers(dest="command")
    comp = sub.add_parser("compile", help="compile a .dab source to JavaScript")
    comp.add_argument("source", type=Path)
    comp.add_argument("-o", "--output", type=Path)
    tokens_command = sub.add_parser("tokens", help="show native lexer tokens")
    tokens_command.add_argument("source", type=Path)
    ast_command = sub.add_parser("ast", help="show dabble's own syntax tree")
    ast_command.add_argument("source", type=Path)
    new = sub.add_parser("new", help="scaffold a minimal browser project")
    new.add_argument("directory", type=Path)
    new.add_argument("--fullstack", action="store_true", help="include a Node.js API server")
    build = sub.add_parser("build", help="compile all .dab files in a project")
    build.add_argument("directory", type=Path, nargs="?", default=Path("."))
    add = sub.add_parser("add", help="install a verified .dab package from a registry")
    add.add_argument("package")
    add.add_argument("--registry", help="registry JSON path or HTTPS URL")
    add.add_argument("--project", type=Path, default=Path("."))
    remove = sub.add_parser("remove", help="remove an installed .dab package")
    remove.add_argument("package")
    remove.add_argument("--project", type=Path, default=Path("."))
    listing = sub.add_parser("list", help="list installed packages")
    listing.add_argument("--project", type=Path, default=Path("."))
    for command, description in (
        ("run", "build and serve a web project (compile once)"),
        ("dev", "develop a web project (recompile on each request)"),
    ):
        parser = sub.add_parser(command, help=description)
        parser.add_argument("directory", type=Path, nargs="?", default=Path("."))
        parser.add_argument("--port", type=int, default=8000)


    # Preserve the original v0.1 CLI: dabble.py file.dab [-o app.js].
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0].endswith(".dab"):
        args.insert(0, "compile")
    ns = cli.parse_args(args)
    try:
        if ns.command == "compile":
            target = compile_file(ns.source, ns.output)
            print(f"Compiled {ns.source} -> {target}")
        elif ns.command in ("tokens", "ast"):
            original = ns.source.read_text(encoding="utf-8")
            try:
                if ns.command == "tokens":
                    for token in syntax.Lexer(original, str(ns.source)).scan():
                        print(f"{token.line}:{token.column + 1} {token.kind:<8} {token.value!r}")
                else:
                    print(syntax.dump(syntax.parse(original, str(ns.source))))
            except SyntaxError as exc:
                raise DabbleError(pretty_error(DabbleError(
                    f"{ns.source}:{exc.lineno}:{exc.offset}: {exc.msg}"), original)) from None
        elif ns.command == "new":
            new_project(ns.directory, fullstack=ns.fullstack)
            print(f"Created {ns.directory} (app.dab, app.js, index.html, styles.css, runtime.js)" + (" + API server" if ns.fullstack else ""))
        elif ns.command == "add":
            add_package(ns.project.resolve(), ns.package, ns.registry)
        elif ns.command == "remove":
            remove_package(ns.project.resolve(), ns.package)
        elif ns.command == "list":
            for name, version in sorted(read_manifest(ns.project.resolve()).get("dependencies", {}).items()):
                print(f"{name}@{version}")
        elif ns.command == "build":
            targets = build_directory(ns.directory)
            ensure_project_assets(ns.directory)
            print(f"Compiled {len(targets)} dabble file(s)")
        elif ns.command in ("run", "dev"):
            if not 0 <= ns.port <= 65535:
                raise DabbleError("--port must be between 0 and 65535")
            serve_project(ns.directory, ns.port, dev=ns.command == "dev")
        else:
            cli.print_help()
            return 0
    except (DabbleError, OSError) as exc:
        print(f"dabble: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
