"""dabble's native lexer, indentation parser and expression parser.

No use of Python's tokenizer, ast, eval or exec: this module parses .dab
source into dabble-owned nodes. The classes intentionally expose stable
fields that are consumed by the JavaScript backend in dabble.py.
"""
from __future__ import annotations
from dataclasses import dataclass
import re


class AST:
    """Base of all dabble syntax tree nodes (zero-based source columns)."""
    def __init__(self, lineno=1, col_offset=0, **fields):
        self.lineno = lineno
        self.col_offset = col_offset
        self.__dict__.update(fields)


def _node(name):
    return type(name, (AST,), {})


for _name in (
    "Module FunctionDef AsyncFunctionDef Assign AugAssign Return Expr If While For Break "
    "Continue Try ExceptHandler Raise Pass Import ImportFrom ClassDef Constant Name Call Attribute BinOp "
    "UnaryOp BoolOp Compare IfExp List Tuple Dict Subscript Lambda JoinedStr FormattedValue "
    "Await keyword arg arguments Add Sub Mult Div Mod Pow FloorDiv Not USub UAdd And Or "
    "Eq NotEq Lt LtE Gt GtE"
).split():
    globals()[_name] = _node(_name)

# Own AST node kinds for dabble-only syntax; the backend can lower them
# using the established function and assignment code-generation pathways.
class ComponentDef(FunctionDef):
    pass

class State(Assign):
    pass

class StyleDef(AST):
    pass

OPS = {"+": Add, "-": Sub, "*": Mult, "/": Div, "%": Mod,
       "**": Pow, "//": FloorDiv, "==": Eq, "!=": NotEq,
       "<": Lt, "<=": LtE, ">": Gt, ">=": GtE}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    column: int   # zero-based


class LexError(SyntaxError):
    pass


def syntax_error(filename, line, col, message):
    # SyntaxError attributes are consumed by dabble's existing diagnostics.
    return LexError(message, (filename, line, col + 1, ""))


def unescape(value: str, filename: str, line: int, col: int) -> str:
    """Decode supported escapes without delegating to Python source parsing."""
    result = []
    i = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"', "0": "\0", "{": "{", "}": "}"}
    while i < len(value):
        if value[i] != "\\":
            result.append(value[i]); i += 1
            continue
        if i + 1 >= len(value):
            raise syntax_error(filename, line, col + i, "unterminated string escape")
        ch = value[i + 1]
        if ch in escapes:
            result.append(escapes[ch]); i += 2
        elif ch in ("u", "U", "x"):
            n = {"u": 4, "U": 8, "x": 2}[ch]
            digits = value[i + 2:i + 2 + n]
            if len(digits) != n or any(c not in "0123456789abcdefABCDEF" for c in digits):
                raise syntax_error(filename, line, col + i, "invalid Unicode escape")
            code = int(digits, 16)
            if code > 0x10ffff:
                raise syntax_error(filename, line, col + i, "invalid Unicode code point")
            result.append(chr(code)); i += 2 + n
        else:
            raise syntax_error(filename, line, col + i, f"unsupported string escape \\{ch}")
    return "".join(result)


class Lexer:
    """A lexer with INDENT/DEDENT tokens and bracket-aware line continuation."""
    MULTI = ("**=", "//=", "==", "!=", "<=", ">=", "//", "**", "+=", "-=", "*=", "/=", "%=")
    SINGLE = "()[]{}.,:+-*/%=<>"
    NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
    IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

    def __init__(self, source, filename="<source>"):
        self.source, self.filename = source, filename

    def scan(self):
        out, indents, brackets = [], [0], []
        lines = self.source.splitlines()
        for row, original in enumerate(lines, 1):
            i = 0
            if not brackets:
                while i < len(original) and original[i] in " \t":
                    if original[i] == "\t":
                        raise syntax_error(self.filename, row, i, "tabs in indentation are not supported; use spaces")
                    i += 1
                if i == len(original) or original[i] == "#":
                    continue
                if i > indents[-1]:
                    indents.append(i)
                    out.append(Token("INDENT", "", row, i))
                elif i < indents[-1]:
                    while len(indents) > 1 and i < indents[-1]:
                        indents.pop()
                        out.append(Token("DEDENT", "", row, i))
                    if i != indents[-1]:
                        raise syntax_error(self.filename, row, i, "inconsistent indentation")
            while i < len(original):
                c = original[i]
                if c in " \t":
                    i += 1; continue
                if c == "#":
                    break
                start = i
                string_prefix = c in "fF" and i + 1 < len(original) and original[i + 1] in "\"'"
                if string_prefix or c in "\"'":
                    kind = "FSTRING" if string_prefix else "STRING"
                    if string_prefix: i += 1
                    quote = original[i]
                    i += 1
                    content_start = i
                    while i < len(original):
                        if original[i] == "\\":
                            i += 2; continue
                        if original[i] == quote:
                            break
                        i += 1
                    if i >= len(original):
                        raise syntax_error(self.filename, row, start, "unterminated string literal")
                    content = original[content_start:i]
                    out.append(Token(kind, content, row, start))
                    i += 1
                    continue
                match = self.NUMBER.match(original, i) if c.isdigit() or (c == "." and i + 1 < len(original) and original[i + 1].isdigit()) else None
                if match:
                    out.append(Token("NUMBER", match.group(), row, start)); i = match.end(); continue
                match = self.IDENT.match(original, i)
                if match:
                    out.append(Token("NAME", match.group(), row, start)); i = match.end(); continue
                op = next((op for op in self.MULTI if original.startswith(op, i)), None)
                if op:
                    out.append(Token(op, op, row, i)); i += len(op); continue
                if c in self.SINGLE:
                    if c in "([{":
                        brackets.append((c, row, i))
                    elif c in ")]}":
                        if not brackets or {"(": ")", "[": "]", "{": "}"}[brackets[-1][0]] != c:
                            raise syntax_error(self.filename, row, i, "mismatched closing bracket")
                        brackets.pop()
                    out.append(Token(c, c, row, i)); i += 1; continue
                raise syntax_error(self.filename, row, i, f"unexpected character {c!r}")
            if not brackets:
                out.append(Token("NEWLINE", "", row, len(original)))
        if brackets:
            _, row, col = brackets[-1]
            raise syntax_error(self.filename, row, col, "unclosed bracket")
        endline = len(lines) + 1
        while len(indents) > 1:
            indents.pop(); out.append(Token("DEDENT", "", endline, 0))
        out.append(Token("EOF", "", endline, 0))
        return out


class Parser:
    """Recursive-descent statements plus Pratt expression parsing."""
    # binding powers; exponentiation is right associative
    INFIX = {"or": 10, "and": 20, "==": 30, "!=": 30, "<": 30, "<=": 30,
             ">": 30, ">=": 30, "+": 40, "-": 40, "*": 50,
             "/": 50, "//": 50, "%": 50, "**": 70}

    def __init__(self, tokens, filename="<source>"):
        self.tokens, self.filename, self.index = tokens, filename, 0

    @property
    def tok(self):
        return self.tokens[self.index]

    def peek(self, n=1):
        return self.tokens[min(self.index + n, len(self.tokens) - 1)]

    def is_(self, kind):
        t = self.tok
        return t.kind == kind or (t.kind == "NAME" and t.value == kind)

    def take(self, kind=None):
        t = self.tok
        if kind and not self.is_(kind):
            self.fail(f"expected {kind!r}, got {t.value or t.kind!r}")
        self.index += 1
        return t

    def accept(self, kind):
        if self.is_(kind):
            return self.take()
        return None

    def fail(self, msg, t=None):
        t = t or self.tok
        raise syntax_error(self.filename, t.line, t.column, msg)

    def node(self, cls, token, **fields):
        return cls(lineno=token.line, col_offset=token.column, **fields)

    def expect_name(self):
        t = self.take("NAME")
        if t.value in {"def", "async", "component", "state", "if", "else", "elif", "while", "for", "in", "return", "try", "except", "finally", "pass", "break", "continue", "raise", "not", "and", "or", "lambda", "await", "True", "False", "None", "as", "class", "import", "from", "style"}:
            self.fail(f"keyword {t.value!r} cannot be used as an identifier", t)
        return t

    def parse(self):
        statements = []
        while not self.is_("EOF"):
            if self.accept("NEWLINE"): continue
            if self.is_("DEDENT"):
                self.fail("unexpected dedent")
            statements.append(self.statement())
        return Module(body=statements)

    def suite(self):
        self.take(":")
        if self.accept("NEWLINE"):
            self.take("INDENT")
            items = []
            while not self.is_("DEDENT"):
                if self.is_("EOF"):
                    self.fail("expected dedent")
                if self.accept("NEWLINE"): continue
                items.append(self.statement())
            self.take("DEDENT")
            if not items:
                self.fail("expected indented statement")
            return items
        # Support a single simple statement on the same line.
        item = self.simple_statement()
        self.take("NEWLINE")
        return [item]

    def statement(self):
        t = self.tok
        if self.is_("async") and self.peek().value == "def":
            self.take("async"); return self.function(t, async_=True)
        if self.is_("def"): return self.function(t)
        if self.is_("component"): return self.function(t, keyword="component")
        if self.is_("style"): return self.style_stmt(t)
        if self.is_("if"):
            self.take(); return self.if_stmt(t)
        if self.is_("while"):
            self.take(); test = self.expression(); body = self.suite()
            alternate = self.suite() if self.accept("else") else []
            return self.node(While, t, test=test, body=body, orelse=alternate)
        if self.is_("for"):
            self.take(); target = self.expression(31)
            self.take("in"); iterable = self.expression(); body = self.suite()
            alternate = self.suite() if self.accept("else") else []
            return self.node(For, t, target=target, iter=iterable, body=body, orelse=alternate)
        if self.is_("try"):
            self.take(); return self.try_stmt(t)
        if self.is_("class"):
            self.take(); self.take("NAME")
            while not self.is_(":") and not self.is_("EOF"): self.take()
            self.suite()
            return self.node(ClassDef, t)
        if self.is_("import") or self.is_("from"):
            return self.import_stmt(t)
        stmt = self.simple_statement()
        self.take("NEWLINE")
        return stmt

    def dotted_name(self):
        parts = [self.expect_name().value]
        while self.accept("."):
            parts.append(self.expect_name().value)
        return ".".join(parts)

    def import_stmt(self, t):
        if self.accept("from"):
            module = self.dotted_name()
            self.take("import")
            names = []
            while True:
                original = self.expect_name().value
                alias = self.expect_name().value if self.accept("as") else original
                names.append((original, alias))
                if not self.accept(","): break
            self.take("NEWLINE")
            return self.node(ImportFrom, t, module=module, names=names)
        self.take("import")
        module = self.dotted_name()
        alias = self.expect_name().value if self.accept("as") else module.split(".")[-1]
        self.take("NEWLINE")
        return self.node(Import, t, module=module, alias=alias)

    def style_stmt(self, t):
        self.take("style")
        name = self.expect_name().value
        self.take(":")
        self.take("NEWLINE")
        self.take("INDENT")
        rules = {"base": {}}
        while not self.is_("DEDENT"):
            if self.accept("NEWLINE"): continue
            key = self.expect_name()
            self.take(":")
            if key.value in ("hover", "focus", "active") and self.accept("NEWLINE"):
                self.take("INDENT")
                section = rules.setdefault(key.value, {})
                while not self.is_("DEDENT"):
                    if self.accept("NEWLINE"): continue
                    prop = self.expect_name().value
                    self.take(":")
                    value = self.expression()
                    if not isinstance(value, Constant) or not isinstance(value.value, (str, int, float)):
                        self.fail("style values must be literal strings or numbers")
                    if prop in section: self.fail("duplicate CSS property")
                    section[prop] = value.value
                    self.take("NEWLINE")
                self.take("DEDENT")
            else:
                value = self.expression()
                if not isinstance(value, Constant) or not isinstance(value.value, (str, int, float)):
                    self.fail("style values must be literal strings or numbers")
                if key.value in rules["base"]: self.fail("duplicate CSS property", key)
                rules["base"][key.value] = value.value
                self.take("NEWLINE")
        self.take("DEDENT")
        return self.node(StyleDef, t, name=name, rules=rules)

    def simple_statement(self):
        t = self.tok
        if self.is_("state"):
            self.take()
            name = self.expect_name()
            self.take("=")
            initial = self.expression()
            built_in = self.node(Name, t, id="signal")
            call = self.node(Call, t, func=built_in, args=[initial], keywords=[])
            return self.node(State, t, targets=[self.node(Name, name, id=name.value)], value=call, initial=initial)
        if self.is_("return"):
            self.take(); value = None if self.is_("NEWLINE") else self.expression()
            return self.node(Return, t, value=value)
        if self.is_("raise"):
            self.take(); value = None if self.is_("NEWLINE") else self.expression()
            cause = self.expression() if self.accept("from") else None
            return self.node(Raise, t, exc=value, cause=cause)
        for name, cls in (("pass", Pass), ("break", Break), ("continue", Continue)):
            if self.accept(name): return self.node(cls, t)
        expression = self.expression()
        if self.is_("="):
            self.take(); value = self.expression()
            if self.is_("="):
                self.fail("multiple assignment targets are unsupported")
            return self.node(Assign, t, targets=[expression], value=value)
        if self.tok.kind in ("+=", "-=", "*=", "/=", "%=", "**=", "//="):
            op = self.take()
            return self.node(AugAssign, t, target=expression, op=OPS[op.value[:-1]](), value=self.expression())
        return self.node(Expr, t, value=expression)

    def function(self, t, async_=False, keyword="def"):
        self.take(keyword)
        name = self.expect_name().value
        self.take("(")
        params = []
        if not self.is_(")"):
            while True:
                ptok = self.expect_name()
                params.append(self.node(arg, ptok, arg=ptok.value, annotation=None))
                if self.accept("="):
                    self.fail("default function arguments are not supported")
                if not self.accept(","): break
                if self.is_(")"): break
        self.take(")")
        body = self.suite()
        a = arguments(args=params, posonlyargs=[], kwonlyargs=[], vararg=None, kwarg=None, defaults=[], kw_defaults=[])
        typ = AsyncFunctionDef if async_ else ComponentDef if keyword == "component" else FunctionDef
        return self.node(typ, t, name=name, args=a, body=body, decorator_list=[], returns=None, type_params=[])

    def if_stmt(self, t):
        test = self.expression(); body = self.suite(); alternate = []
        if self.is_("elif"):
            nt = self.take(); alternate = [self.if_stmt(nt)]
        elif self.accept("else"):
            alternate = self.suite()
        return self.node(If, t, test=test, body=body, orelse=alternate)

    def try_stmt(self, t):
        body = self.suite(); handlers = []; finalbody = []; alternate = []
        while self.is_("except"):
            eh = self.take(); typ = None; name = None
            if not self.is_(":"):
                typ = self.expression()
                if self.accept("as"):
                    name = self.expect_name().value
            handlers.append(self.node(ExceptHandler, eh, type=typ, name=name, body=self.suite()))
        if self.accept("else"):
            alternate = self.suite()
        if self.accept("finally"):
            finalbody = self.suite()
        if not handlers and not finalbody:
            self.fail("try requires except or finally", t)
        return self.node(Try, t, body=body, handlers=handlers, finalbody=finalbody, orelse=alternate)

    def expression(self, min_bp=0):
        t = self.take()
        if t.kind == "NUMBER":
            try: val = float(t.value) if any(c in t.value for c in ".eE") else int(t.value)
            except ValueError: self.fail("invalid numeric literal", t)
            left = self.node(Constant, t, value=val)
        elif t.kind == "STRING":
            left = self.node(Constant, t, value=unescape(t.value, self.filename, t.line, t.column))
        elif t.kind == "FSTRING":
            left = self.fstring(t)
        elif t.kind == "NAME":
            if t.value in ("True", "False", "None"):
                left = self.node(Constant, t, value={"True": True, "False": False, "None": None}[t.value])
            elif t.value in ("not", "await"):
                right = self.expression(25 if t.value == "not" else 60)
                left = self.node(UnaryOp, t, op=Not(), operand=right) if t.value == "not" else self.node(Await, t, value=right)
            elif t.value == "lambda":
                params = []
                if not self.is_(":"):
                    while True:
                        param = self.expect_name(); params.append(self.node(arg, param, arg=param.value, annotation=None))
                        if not self.accept(","): break
                self.take(":")
                body = self.expression(0)
                a = arguments(args=params, posonlyargs=[], kwonlyargs=[], vararg=None, kwarg=None, defaults=[], kw_defaults=[])
                left = self.node(Lambda, t, args=a, body=body)
            elif t.value in ("and", "or", "if", "else", "in", "as", "def", "return", "for", "while"):
                self.fail(f"unexpected keyword {t.value!r}", t)
            else:
                left = self.node(Name, t, id=t.value)
        elif t.kind in ("+", "-"):
            left = self.node(UnaryOp, t, op=(UAdd() if t.kind == "+" else USub()), operand=self.expression(60))
        elif t.kind == "(":
            if self.accept(")"):
                left = self.node(Tuple, t, elts=[])
            else:
                first = self.expression()
                if self.accept(","):
                    items = [first]
                    while not self.is_(")"):
                        items.append(self.expression())
                        if not self.accept(","): break
                    self.take(")")
                    left = self.node(Tuple, t, elts=items)
                else:
                    self.take(")"); left = first
        elif t.kind == "[":
            values = self.expression_list("]")
            left = self.node(List, t, elts=values)
        elif t.kind == "{":
            keys, vals = [], []
            while not self.is_("}"):
                keys.append(self.expression()); self.take(":"); vals.append(self.expression())
                if not self.accept(","): break
            self.take("}")
            left = self.node(Dict, t, keys=keys, values=vals)
        else:
            self.fail(f"expected expression, got {t.value or t.kind!r}", t)

        while True:
            t = self.tok
            if t.kind in ("(", "[", ".") and 90 >= min_bp:
                self.take()
                if t.kind == "(":
                    args, kwargs, saw_kw = [], [], False
                    while not self.is_(")"):
                        if self.tok.kind == "NAME" and self.peek().kind == "=":
                            key = self.take(); self.take("=")
                            kwargs.append(self.node(keyword, key, arg=key.value, value=self.expression()))
                            saw_kw = True
                        else:
                            if saw_kw: self.fail("positional argument after keyword argument")
                            args.append(self.expression())
                        if not self.accept(","): break
                    self.take(")")
                    left = self.node(Call, t, func=left, args=args, keywords=kwargs)
                elif t.kind == "[":
                    key = self.expression(); self.take("]")
                    left = self.node(Subscript, t, value=left, slice=key)
                else:
                    attr = self.expect_name()
                    left = self.node(Attribute, t, value=left, attr=attr.value)
                continue
            if self.is_("if") and min_bp <= 5:
                self.take(); condition = self.expression(6)
                self.take("else"); other = self.expression(5)
                left = self.node(IfExp, t, body=left, test=condition, orelse=other)
                continue
            op = t.value if t.kind == "NAME" else t.kind
            bp = self.INFIX.get(op)
            if bp is None or bp < min_bp: break
            self.take()
            right = self.expression(bp if op == "**" else bp + 1)
            if op in ("and", "or"):
                cls = And if op == "and" else Or
                if isinstance(left, BoolOp) and isinstance(left.op, cls):
                    left.values.append(right)
                else:
                    left = self.node(BoolOp, t, op=cls(), values=[left, right])
            elif op in ("==", "!=", "<", "<=", ">", ">="):
                if isinstance(left, Compare):
                    self.fail("chained comparisons are not yet supported", t)
                left = self.node(Compare, t, left=left, ops=[OPS[op]()], comparators=[right])
            else:
                left = self.node(BinOp, t, left=left, op=OPS[op](), right=right)
        return left

    def expression_list(self, close):
        items = []
        while not self.is_(close):
            items.append(self.expression())
            if not self.accept(","): break
        self.take(close)
        return items

    def fstring(self, t):
        raw = t.value
        parts = []; literal = []; i = 0
        def flush():
            if literal:
                text = unescape("".join(literal), self.filename, t.line, t.column)
                parts.append(self.node(Constant, t, value=text))
                literal.clear()
        while i < len(raw):
            if raw[i:i+2] in ("{{", "}}"):
                # Keep escaping semantics distinct from unescape() processing.
                literal.append(raw[i])
                i += 2; continue
            if raw[i] == "}":
                self.fail("unmatched '}' in f-string", t)
            if raw[i] != "{":
                literal.append(raw[i]); i += 1; continue
            flush(); i += 1; start = i
            # Handle function calls and dictionary strings inside placeholders.
            depth = 0; quote = None
            while i < len(raw):
                c = raw[i]
                if quote:
                    if c == "\\": i += 2; continue
                    if c == quote: quote = None
                elif c in "\"'": quote = c
                elif c in "([{" : depth += 1
                elif c in ")]}":
                    if c == "}" and depth == 0: break
                    depth -= 1
                i += 1
            if i >= len(raw): self.fail("unclosed '{' in f-string", t)
            snippet = raw[start:i].strip()
            convert = -1
            if snippet.endswith("!s"):
                snippet = snippet[:-2].rstrip(); convert = 115
            if not snippet:
                self.fail("empty expression in f-string", t)
            subparser = Parser(Lexer(snippet, self.filename).scan(), self.filename)
            expression = subparser.expression()
            if not subparser.is_("NEWLINE") and not subparser.is_("EOF"):
                self.fail("unsupported format specifier or expression in f-string", t)
            parts.append(self.node(FormattedValue, t, value=expression, conversion=convert, format_spec=None))
            i += 1
        flush()
        return self.node(JoinedStr, t, values=parts)


def parse(source, filename="<source>"):
    return Parser(Lexer(source, filename).scan(), filename).parse()


def dump(node):
    """Human-readable native AST; useful for debugging dabble source."""
    if isinstance(node, AST):
        fields = ((k, v) for k, v in vars(node).items() if k not in ("lineno", "col_offset"))
        return f"{type(node).__name__}(" + ", ".join(f"{k}={dump(v)}" for k, v in fields) + ")"
    if isinstance(node, list):
        return "[" + ", ".join(dump(item) for item in node) + "]"
    return repr(node)
