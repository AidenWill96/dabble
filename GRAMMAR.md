# dabble 0.5 Grammar Additions

This file supplements the existing native parser grammar implemented in `dabble_syntax.py`. The parser is owned by dabble, not Python's AST module. The following is schematic EBNF (not generated from a parser generator).

```ebnf
module           = { statement } ;
statement        = import | style | existing_statement ;
import           = "import" dotted_name [ "as" name ] NEWLINE
                 | "from" dotted_name "import" import_symbol { "," import_symbol } NEWLINE ;
import_symbol    = name [ "as" name ] ;
dotted_name      = name { "." name } ;
style            = "style" name ":" NEWLINE INDENT style_entries DEDENT ;
style_entries    = { css_declaration | pseudo_section } ;
css_declaration  = name ":" (string | number) NEWLINE ;
pseudo_section   = ("hover" | "focus" | "active") ":" NEWLINE INDENT { css_declaration } DEDENT ;
```

- `style` blocks are module-level and apply only when the component with the matching name is compiled in the same module.
- Python-style indentation is mandatory. Tabs for indentation are rejected.
- `style` values must be static literal strings/numbers; dynamic expressions are not supported.
- `import` statements must be module-level, and resolve to `.dab` files inside the project directory or `dabble_packages/`.
- The compiler builds an ordered dependency graph and compiles imported modules into one self-contained JavaScript bundle.
- Existing `component`, `state`, `def`, `async def`, `if`, `while`, `for`, `try`, `except`, `finally`, `lambda`, dictionaries, lists, arithmetic and expression features are retained.

For every feature and its limitations, see `LANGUAGE.md`.
