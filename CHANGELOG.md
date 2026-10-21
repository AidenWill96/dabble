# Changelog

## 0.5.0 (preview)

- Add `style Component:` blocks for literal CSS declarations and `hover`, `focus`, `active` pseudo-states. Styles are scoped to a component's root HTML element and emitted as `app.css`.
- Add module-level `import mod [as alias]` and `from mod import symbol [as alias]` including dotted paths. Bundle modules into entry JavaScript and reject missing/cyclic dependencies.
- Add Node.js-backed `server.dab` for same-origin JSON API routes using `route(method, path, handler)`; support async handlers, query/body/params, JSON POST, and static frontend serving.
- Add `dabble add`, `dabble remove`, `dabble list` with built-in example package registry, optional local/HTTPS registries, `dabble.json`, `dabble.lock`, SHA-256 verification and source vendoring.
- Add PyInstaller standalone executable build script and GitHub Actions workflow for macOS arm64/x86_64, Linux and Windows. No prebuilt macOS executable is included in this source distribution.
- Preserve v0.4.1 CLI behaviour and browser runtime APIs. The Node runtime is only required for full-stack projects.

## 0.4.1

- Separate CSS starter files, template migration and asset generation in CLI.
