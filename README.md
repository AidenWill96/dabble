# dabble 0.5.0

**dabble** is an experimental Python-inspired language for the web. It uses a native dabble lexer and parser to compile `.dab` files to JavaScript, with built-in reactive components. Release 0.5 adds component-scoped CSS, Node.js HTTP APIs, local imports, a preliminary package manager, and a reproducible standalone executable build pipeline.

## Quick start

Install the CLI from the wheel (`pipx install dabble_lang-0.5.0-py3-none-any.whl`) or use the OS-specific **standalone executable** from a CI build (no Python installation needed to run that executable).

```sh
dabble new hello --fullstack
dabble dev hello
# http://127.0.0.1:8000
# API: http://127.0.0.1:8000/api/hello
```

`dabble new hello` without `--fullstack` creates a frontend-only app. The full-stack server requires **Node.js 18+**, but the browser-only compiler and dev server do not require Node.js. The wheel requires Python >=3.10; a PyInstaller-built executable bundles its own interpreter.

## Supported commands

| Command | Action |
|---|---|
| `dabble new my-site` | Create browser project |
| `dabble new my-site --fullstack` | Create browser + API project |
| `dabble dev [directory] --port 8000` | Rebuild and serve (automatically restarts backend on `.dab` changes) |
| `dabble run [directory] --port 8000` | Compile once and serve |
| `dabble build [directory]` | Compile `.dab` files and prepare assets |
| `dabble compile path/to/file.dab` | Compile one file and emit `.js` and `.css` |
| `dabble tokens file.dab` | Inspect lexer tokens |
| `dabble ast file.dab` | Inspect AST |
| `dabble add greetings` | Install sample package from bundled registry |
| `dabble add name --registry registry.json` | Install package from user-supplied registry |
| `dabble remove greetings` | Remove package if unmodified |
| `dabble list` | Show packages declared in `dabble.json` |
| `dabble --version` | Print version |

## Native styles

```python
style Card:
    background_color: "#162b41"
    color: "white"
    padding: "1.5rem"
    border_radius: "1rem"
    hover:
        background_color: "#244565"

component Card():
    return section(h2("Hello dabble"), p("Native, scoped styling."))

mount("#app", Card)
```

`style Card` generates `.dabble-Card { ... }` CSS, and the styled `Card` component gets a `dabble-Card` class on its single root HTML element. Style values must be literal strings or numbers; property names use underscores in place of hyphens. `hover`, `focus`, `active` states are supported. The compiler produces `app.css`, and the CLI inserts its stylesheet link into `index.html` when necessary. Existing custom `styles.css` is **never replaced**. Styling imported components is included in the importing entry's generated CSS. This is not CSS-in-JS, CSS variables/at-rules/animations are not directly supported in native syntax yet; use `styles.css` for advanced CSS.

## Local modules

In `shared.dab`:

```python
def title():
    return "Hello from a shared dabble module"
```

In `app.dab`:

```python
from shared import title
# Or: import shared as common
mount("#app", lambda: h1(title()))
```

Local `.dab` files and installed packages in `dabble_packages/` are recursively bundled into each generated entry JavaScript file. Files can be addressed as dotted paths, for example `from components.card import Card`. Top-level functions and simple assignments are exported unless their names start with `_`. Imports are **top-level only**; cyclic imports and missing exports are rejected with diagnostics. No Python/Node built-in module imports or external npm modules can be imported through this syntax.

## Backend APIs

Create `server.dab` in the same directory:

```python
def hello(req):
    return {"message": "Hello from dabble", "name": req.query["name"]}

async def echo(req):
    return {"received": req.body}

route("GET", "/api/hello", hello)
route("POST", "/api/echo", echo)
```

The CLI compiles `server.dab` to `server.js` and launches it with **Node.js**. The backend and frontend are served on the **same origin and port**. Handlers receive a request object with `.method`, `.path`, `.query`, `.params`, `.body` and `.headers` fields, and return JSON-serialisable data (sync or async). `GET`, `POST`, `PUT`, `PATCH`, `DELETE` and `/api/...` paths are supported, including `:param` route segments. Request bodies must contain valid JSON up to 1 MiB. API responses return HTTP 200; to configure response status, middleware, cookies, database access, or auth, you will need a future release or a separate backend.

Browser code may fetch the endpoint using `fetch_json("/api/hello?name=world")` and `post_json("/api/echo", {"ok": True})`. The Node server refuses to serve `.dab` sources, package source, project manifests, and `.cjs` backend runtime files.

## Packages

`dabble add greetings` installs an example package in `dabble_packages/greetings.dab`, updates `dabble.json`, and writes a SHA-256-pinned `dabble.lock`. You can then write `from greetings import hello`. `dabble add ui_helpers` provides reusable HTML builders.

The registry is **initially bundled**, not a public package ecosystem. External registries can be specified using a JSON file or HTTPS URL. Example schema:

```json
{
  "packages": {
    "greetings": {
      "version": "0.1.0",
      "source": "def hello(name):\n    return f\"Hello, {name}!\"\n",
      "sha256": "<64-character lowercase SHA-256 hex digest of UTF-8 source>"
    }
  }
}
```

Package integrity is checked against the registry metadata; it **does not prove a registry is trustworthy**. Only install packages from sources you trust. No dependency solving, publishing service, update command, signatures, or sandboxing is included yet.

## Files generated by dabble

```text
my-site/
├── app.dab               source for frontend
├── app.js                generated frontend bundle
├── app.css               generated native style CSS
├── index.html            website entry point
├── styles.css            custom CSS (never overwritten)
├── runtime.js            dabble browser runtime
├── server.dab            optional backend source
├── server.js             generated backend bundle
├── dabble_server.cjs     backend runtime, generated if needed
├── dabble.json           package manifest
├── dabble.lock           optional package lockfile
└── dabble_packages/      installed dabble packages
```

Keep `app.dab`, `server.dab`, `styles.css`, `index.html`, `dabble.json` and `dabble.lock` in version control. Generated `.js`/`app.css` can be rebuilt. The `dabble_packages/` folder is vendor-style source and can also be committed for reproducibility.

## Standalone executable (no Python runtime required)

The **build process** uses PyInstaller to bundle the compiler and Python runtime into a platform-specific executable. The resulting program can run on a target machine without Python installed.

Build on macOS (Python is needed **only on the build machine**):

```sh
python3 -m pip install pyinstaller
python3 tools/build_standalone.py
./standalone/dabble --version
```

Alternatively, use the included `.github/workflows/standalone.yml` with GitHub Actions to build macOS Apple Silicon, Intel macOS, Linux and Windows executables. Binaries must be built on their native OS; this Linux build environment cannot produce a tested macOS executable. For macOS users, do **not** use the included Linux binary, if any. On first use, macOS Gatekeeper may require approval for an unsigned executable; the pipeline does not implement signing/notarisation.

Node.js is **still needed** to execute a `server.dab` backend. A standalone compiler removes only the need for Python.

## Test

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -q
node tests/test_runtime.cjs
node tests/test_runtime_v02.cjs
node tests/test_runtime_v05.cjs
```

## Status and limitations

This is experimental software, not production hardened. No type checker, integrated debugger, advanced module visibility rules, package dependency solver, native database driver, SSR, or built-in deployment system. The semantics are Python-inspired but not Python-compatible. See [LANGUAGE.md](LANGUAGE.md) and [GRAMMAR.md](GRAMMAR.md).
