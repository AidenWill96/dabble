#!/usr/bin/env python3
"""Build a Python-free-at-runtime executable for the HOST operating system.

Requires Python 3.10+, PyInstaller to BUILD, but NOT to RUN the binary.
Run from repo root: python tools/build_standalone.py
macOS binaries must be built on macOS (CI workflow also provided).
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if importlib.util.find_spec('PyInstaller') is None:
    raise SystemExit('PyInstaller is required for building: python -m pip install pyinstaller')
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
    '--name', 'dabble', '--hidden-import', 'dabble_assets', '--collect-all', 'dabble_assets',
    '--distpath', str(ROOT / 'standalone'), str(ROOT / 'dabble.py')]
subprocess.run(command, cwd=ROOT, check=True)
print('Standalone binary written to', ROOT / 'standalone')
