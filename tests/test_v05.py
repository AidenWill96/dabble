"""End-to-end behaviour added in dabble 0.5."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from dabble import DabbleError, compile_file, build_directory, new_project, add_package, remove_package, ensure_project_assets, Compiler
from dabble_syntax import parse, StyleDef, ImportFrom

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(ROOT / 'dabble.py')]

class V05Tests(unittest.TestCase):
    def test_native_grammar(self):
        tree = parse('''from text import greet as hi
import cards as ui
style Card:
    background_color: "navy"
    hover:
        color: "white"
component Card():
    return div("Hi")
''')
        self.assertIsInstance(tree.body[0], ImportFrom)
        self.assertIsInstance(tree.body[2], StyleDef)
        self.assertEqual(tree.body[2].rules['hover']['color'], 'white')

    def test_styled_component_css_and_generated_js(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.dab').write_text('''style Card:
    color: "white"
    background_color: "#123"
    hover:
        color: "yellow"
component Card():
    return section("Hello")
mount("#app", Card)
''')
            compile_file(root / 'app.dab')
            self.assertIn('scope(__dabble.section', (root/'app.js').read_text())
            css = (root/'app.css').read_text()
            self.assertIn('.dabble-Card:hover', css)
            self.assertIn('background-color: #123;', css)
            ensure_project_assets(root)
            self.assertIn('href="app.css"', (root / 'index.html').read_text())

    def test_styling_in_existing_custom_html_preserves_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'index.html').write_text('<html><head><title>Custom</title></head><body>Custom</body></html>')
            (root/'app.dab').write_text('style Main:\n    padding: "2rem"\ncomponent Main():\n    return main("Hey")\n')
            compile_file(root/'app.dab'); ensure_project_assets(root)
            html=(root/'index.html').read_text()
            self.assertIn('<title>Custom</title>',html)
            self.assertIn('href="app.css"',html)
            ensure_project_assets(root)
            self.assertEqual(html,(root/'index.html').read_text())

    def test_css_injection_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'app.dab').write_text('style Card:\n    color: "red; } body { display:none"\n')
            with self.assertRaisesRegex(DabbleError,'unsafe CSS'):
                compile_file(root/'app.dab')

    def test_modules_resolve_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'utils.dab').write_text('def greet(name):\n    return f"Hi {name}"\n')
            (root/'app.dab').write_text('from utils import greet\nprint(greet("world"))\n')
            compile_file(root/'app.dab')
            js=(root/'app.js').read_text()
            self.assertIn('function greet(name)',js)
            self.assertIn('["greet"]',js)
            self.assertIn('Hi ',js)

    def test_undefined_export_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'lib.dab').write_text('def good():\n    return True\n')
            (root/'app.dab').write_text('from lib import missing\n')
            with self.assertRaisesRegex(DabbleError,'not exported'):
                compile_file(root/'app.dab')

    def test_missing_module_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'app.dab').write_text('from no_such_module import x\n')
            with self.assertRaisesRegex(DabbleError,'app.dab:1:1:'):
                compile_file(root/'app.dab')

    def test_module_cycle_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'a.dab').write_text('import b\n')
            (root/'b.dab').write_text('import a\n')
            with self.assertRaisesRegex(DabbleError,'circular'):
                compile_file(root/'a.dab')

    def test_nested_import_disallowed(self):
        with self.assertRaisesRegex(DabbleError,'module scope'):
            Compiler('broken.dab', import_ids={'lib':'lib'}).compile('def f():\n    import lib\n')

    def test_package_install_import_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            new_project(root/'site')
            site=root/'site'
            add_package(site,'greetings')
            self.assertEqual(json.loads((site/'dabble.json').read_text())['dependencies']['greetings'],'0.1.0')
            (site/'app.dab').write_text('from greetings import hello\nprint(hello("Dabble"))\n')
            compile_file(site/'app.dab')
            self.assertIn('Hello, ', (site/'app.js').read_text())
            remove_package(site,'greetings')
            self.assertFalse((site/'dabble_packages/greetings.dab').exists())

    def test_package_integrity_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'reg.json').write_text(json.dumps({'packages':{'fake':{'version':'1.0.0','source':'print("oops")\n','sha256':'0'*64}}}))
            with self.assertRaisesRegex(DabbleError,'sha256 mismatch'):
                add_package(root,'fake',str(root/'reg.json'))

    def test_modified_package_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            add_package(root,'greetings')
            (root/'dabble_packages/greetings.dab').write_text('print("changed")\n')
            with self.assertRaisesRegex(DabbleError,'modified'):
                remove_package(root,'greetings')

    def test_fullstack_http_and_source_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)/'site'
            new_project(site, fullstack=True)
            (site/'server.dab').write_text('''def response(req):
    return {"message": "works", "name": req.query["name"]}

def echo(req):
    return req.body

route("GET", "/api/greet", response)
route("POST", "/api/echo", echo)
''')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            proc = subprocess.Popen(CLI+['run',str(site),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for i in range(65):
                    if proc.poll() is not None: self.fail('server exited early')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/api/greet?name=world',timeout=.3) as r:
                            result=json.load(r); break
                    except OSError: time.sleep(.08)
                else: self.fail('server failed to start')
                self.assertEqual(result['message'],'works')
                self.assertEqual(result['name'],'world')
                req=Request(f'http://127.0.0.1:{port}/api/echo',json.dumps({'ok':True}).encode(),{'Content-Type':'application/json'},'POST')
                self.assertEqual(json.load(urlopen(req))['ok'],True)
                self.assertEqual(urlopen(f'http://127.0.0.1:{port}/app.js').status,200)
                for private in ['server.dab','dabble.json','dabble_server.cjs','dabble_packages/greetings.dab']:
                    with self.assertRaises(HTTPError) as cm:
                        urlopen(f'http://127.0.0.1:{port}/{private}')
                    self.assertIn(cm.exception.code,(403,404))
            finally:
                proc.terminate(); proc.wait(timeout=4)

if __name__=='__main__': unittest.main()
