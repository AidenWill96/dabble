# DABBLE_AI_GUIDE.md — dabble 0.5.0 authoritative AI reference

**Read this before writing or debugging dabble.** This document describes actual implemented behaviour in the **dabble 0.5.0 preview** implementation. Do not assume `.dab` is Python, and do not invent framework APIs, package names, or CSS syntax.

## 1. Identity

- Language: **dabble**, extension `.dab`; Python-like indentation, dedicated native lexer/recursive-descent parser and dabble AST.
- Compiler written in Python >=3.10; it generates browser JavaScript or Node.js JavaScript; **`.dab` programs do not execute in Python**.
- Runtime: `runtime.js` in browser; `dabble_server.cjs` on Node.js 18+ in full-stack projects.
- CLI: `dabble`, installed from the wheel or packaged by PyInstaller into a standalone executable. The `dabble` executable generated with PyInstaller bundles Python and does not require an installed Python interpreter on the user's machine. **This repository provides a build workflow, not prebuilt macOS executables.**
- Current release is experimental; no type checker, native database integration, package solver, or official hosted package index.

## 2. CLI: exact commands

```sh
dabble new my-site
dabble new full-site --fullstack
dabble dev my-site
dabble dev full-site --port 3000
dabble run my-site
dabble build my-site
dabble compile my-site/app.dab
dabble tokens my-site/app.dab
dabble ast my-site/app.dab
dabble add greetings                # when cwd is a dabble project
dabble add ui_helpers
dabble add custom --registry path/to/registry.json
dabble remove greetings
dabble list
dabble --version
```

`run` compiles once and serves. `dev` recompiles source (browser-only on each HTTP request; backend projects watch for changes and restart the Node backend after successful recompilation). `new` creates app and starter assets. `build` compiles the `.dab` files and creates missing assets; `app.js` and `app.css` are generated. For a `server.dab`, `server.js` and `dabble_server.cjs` are generated. Without `server.dab`, no Node.js is needed to run dabble projects.

## 3. Canonical project layout

```text
site/
  app.dab                main browser entry
  shared.dab             optional locally imported module
  app.js                 generated browser bundle
  app.css                generated CSS for `style` blocks
  index.html             browser entry file
  styles.css             manually authored design CSS (preserved)
  runtime.js             browser runtime
  server.dab             optional API/backend entry
  server.js              generated Node.js bundle
  dabble_server.cjs      Node backend runtime
  dabble.json            package manifest
  dabble.lock            optional lockfile
  dabble_packages/       vendor-installed source packages
```

Note on user reports that the site looks unstyled: `dabble new` creates *default* `styles.css` and `index.html`; copying an unrelated site's `app.dab` does NOT reconstruct its external stylesheet. Either copy its `styles.css` or move rules into `style` blocks. Native style rules generate `app.css`, which is linked by the CLI, without overwriting custom CSS.

## 4. Core supported language

- Variable: `x = 10`.
- Function: `def greet(name):` and `return`.
- Async function: `async def load():` + `await fetch_json(...)`.
- Components: `component App():` returns one DOM element (for scoped native styles), invoked via `mount("#app", App)`.
- Reactive state: `state count = 0`, which compiles to a signal; change via `count.set(next)`; read via `count.get()`; passing the signal as an element child produces reactive text.
- Reactive APIs: `signal(initial)`, `computed(lambda: expression)`, `effect(lambda: side_effect)`.
- Control flow: `if`, `elif`, `else`, `while`, `for item in items`, `break`, `continue`, `try`, one `except`, optional `finally`, `raise`, `pass`.
- Operators: normal arithmetic and comparisons, `and`, `or`, `not`, ternary `x if condition else y`.
- Values: strings (single/double quoted), supported f-strings, numbers, bools `True`/`False`, `None`, lists `[]`, tuples (compiled to JS arrays), dictionaries `{ "key": value }`.
- Built-ins: `len`, `range`, `str`, `print`. JS DOM build functions for `div`, `h1`, `p`, `button`, `input_`, etc.; `el("custom-tag", ...)` for arbitrary HTML tags.
- HTML element properties use named arguments, like `button("Click", onclick=handler, class_="primary")`. Use underscores for Python-compatible parameter names (`class_`, `html_for`, `aria_label`, `data_id`).
- Browser APIs: `fetch_json(url)`, `post_json(url, data)`, `storage_get(key, fallback)`, `storage_set(key, value)`, `navigate("/about")`, `path()`, `on_route(callback)`.
- Don't use Python `import os`, built-in `open`, `requests`, `pip`, `dict.get` on every value, decorators, class definitions, generators, decorators, Python standard library or pip packages. Python language features not explicitly documented are NOT automatically supported.

## 5. Native component-scoped CSS (NEW 0.5)

```python
style Card:
    background_color: "#102a43"
    padding: "1.5rem"
    color: "white"
    border_radius: "12px"
    hover:
        background_color: "#294861"
    focus:
        outline: "2px solid cyan"

component Card():
    return article(h2("Title"), p("Description"))

mount("#app", Card)
```

Rules:

- `style Name:` must be **top-level** and applies to a `component Name` in the *same* `.dab` module. The component's root element gets CSS class `dabble-Name` via `__dabble.scope`.
- CSS is generated into the compiled entry's `.css` file (normally `app.css`), and imported modules' native styles are included.
- Property names must be alphanumeric/underscore, starting with a lowercase letter; underscores generate hyphens (`background_color` -> `background-color`). CSS values must be **literal strings or numbers**, not signals/expressions. Semicolons, CSS declaration blocks and control characters are disallowed in values.
- Supported nested pseudo-states: `hover:`, `focus:`, `active:` with their own declarations. No custom descendant selectors, keyframes, `@media`/`@supports`, nested components, interpolated dynamic CSS, or other selector syntax in this release. Use manually authored `styles.css` for advanced designs; it is preserved by the CLI.
- Styled components must return **one root element**. Returning an array/string under styled root throws a runtime error.

## 6. Local module system (NEW 0.5)

Create `shared.dab`:

```python
def welcome(person):
    return f"Welcome, {person}!"
```

Use from `app.dab`:

```python
from shared import welcome
# or: import shared as helpers
mount("#app", lambda: h1(welcome("friend")))
```

- Imports only at module scope. Both `import shared [as alias]` and `from shared import name [as alias]`; supports dotted names like `from widgets.card import Card` resolving `widgets/card.dab`.
- Resolution searches project root first, then `dabble_packages`. Imported code is bundled into the generated entry JavaScript. Each module is evaluated once, and its top-level functions and simple bindings are exported unless prefixed `_`.
- Cycles, absent modules or non-exported symbols produce compile-time errors.
- No Python imports, npm imports, wildcard imports, version imports, or filepaths outside the current project/package roots.
- Browser modules must use browser-compatible APIs; backend modules must use backend-compatible APIs; simple pure dabble functions can be shared.

## 7. Full-stack backend (NEW 0.5)

`server.dab`:

```python
from shared import welcome

def greet(req):
    return {"message": welcome(req.query["name"])}

async def echo(req):
    return {"data": req.body}

route("GET", "/api/greet", greet)
route("POST", "/api/echo", echo)
```

`app.dab`:

```python
component App():
    state answer = "Loading..."

    async def load():
        data = await fetch_json("/api/greet?name=Ada")
        answer.set(data["message"])

    return main(button("Load", onclick=load), p(answer))

mount("#app", App)
```

- `dabble dev project` runs a Node.js-backed HTTP server on `127.0.0.1` serving both `/api` endpoints and static frontend files (same origin), generated by `server.dab`. Node 18+ must be installed; there are **no npm dependencies** in the runtime.
- API route signature: `route(method, "/api/path", handler)` with methods `GET`, `POST`, `PUT`, `PATCH`, `DELETE`. Dynamic `:param` path segments supported. Handlers may be synchronous or asynchronous and receive request `.method`, `.path`, `.query`, `.params`, `.body`, `.headers`.
- Always return a JSON-serialisable result. Current route handler responses are HTTP **200** by default, 400 for malformed JSON, 413 oversized JSON and 500 for unhandled handler errors. Custom statuses/headers/cookies are *not yet exposed*.
- Body size limit 1 MiB; accepted body content JSON. The static server blocks `.dab`, `.cjs`, project manifest and vendored source paths. This is early-stage and should be security-reviewed before internet deployment.
- `route` is for server code, not a browser built-in. Browser code uses `fetch_json`, `post_json`, etc. Full-stack code is not the same as Python Flask or Node Express.

## 8. Package manager (NEW 0.5)

```sh
cd my-site
dabble add greetings
dabble list
dabble remove greetings
```

Built-in package names in v0.5: `greetings` (exports `hello(name)`), `ui_helpers` (exports `heading(title)`, `card(content)`).

`dabble add greetings` creates `dabble_packages/greetings.dab`, updates `dabble.json` and writes a `dabble.lock` with SHA-256; then write `from greetings import hello`. A custom index can be a local JSON file or HTTPS URL provided via `--registry`. It must contain `packages` object entries with `source` (complete module source text), `version`, and `sha256` (64 hex characters matching UTF-8 source). The default bundled registry is only a demonstration; there is **no public package registry yet**. Downloads are not sandboxed. Integrity checks prevent mismatched bytes but do not authenticate the registry itself. No transitive package dependency solving or authenticated package publishing.

## 9. Executable distribution (NEW 0.5 tooling)

`tools/build_standalone.py` builds a native single-file PyInstaller executable for the **host OS**. Python and PyInstaller are needed on the *build machine*, not to run the resulting binary. macOS must be built on macOS; the included `.github/workflows/standalone.yml` automates macOS arm64/Intel, Windows and Linux binaries through GitHub Actions. No prebuilt/tested macOS binary is included with the provided source ZIP. macOS binaries may need signing/notarisation or Gatekeeper approval. Running a backend still requires Node.js on the user's computer.

## 10. Fully working example

See `examples/fullstack/` in the source release for the tested app comprising `app.dab`, `server.dab`, `shared.dab`, compiled JS/CSS and HTML. The frontend demonstrates `style`, `component`, `state`, `async/await`, HTTP access and module imports. Its backend demonstrates JSON API routing and shared modules. To launch from the parent directory: `dabble dev examples/fullstack`.

## 11. Grounded advice for AI assistants

- When asked about unknown syntax, first inspect `LANGUAGE.md`, `dabble_syntax.py` and `dabble.py` from the current version. Do not borrow features blindly from Python, React, TypeScript, Django or Svelte.
- When debugging a blank site, check browser console, `runtime.js` inclusion before `app.js`, `mount("#app", ...)`, and the current `index.html` content.
- When debugging missing CSS, distinguish `styles.css` (user-maintained, *not* generated from app source) from `app.css` (generated from `style` blocks). Check the `<link rel="stylesheet" href="app.css">` tag.
- On macOS: the wheel installer can be installed with `pipx install /full/path/dabble_lang-0.5.0-py3-none-any.whl` if Python is installed. The standalone binary avoids Python at runtime, but must first be built on macOS or downloaded from a successful macOS CI build.
- For backend failures check `node --version` and errors printed by `dabble dev`.
- Do not describe dabble 0.5 as fully production-ready, possessing a public package registry, having a prebuilt macOS binary, or running backend APIs with no Node.js installed.

---

Guide source: dabble v0.5 native parser/compiler, browser runtime, Node backend, CLI, package manager, regression/integration tests.
