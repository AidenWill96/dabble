"""Version 0.2 regression tests."""
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dabble import Compiler, DabbleError, compile_file, new_project, build_directory


class CompilerV02Tests(unittest.TestCase):
    def compile(self, code):
        return Compiler("feature.dab").compile(code)

    def test_async_and_await(self):
        generated = self.compile('''async def load():
    data = await fetch_json("/api/data")
    return data["title"]
''')
        self.assertIn("async function load()", generated)
        self.assertIn('(await __dabble.fetch_json("/api/data"))', generated)

    def test_await_outside_async_rejected(self):
        with self.assertRaisesRegex(DabbleError, "await is only valid"):
            self.compile('def bad():\n    return await fetch_json("/api")\n')

    def test_nested_sync_cannot_await(self):
        with self.assertRaisesRegex(DabbleError, "await is only valid"):
            self.compile('async def outer():\n    def inner():\n        return await fetch_json("/api")\n')

    def test_async_inside_sync(self):
        generated = self.compile('def outer():\n    async def inner():\n        return await fetch_json("/api")\n    return inner\n')
        self.assertIn('async function inner()', generated)

    def test_try_except_finally(self):
        generated = self.compile('''async def load():
    try:
        data = await fetch_json("/api")
    except Exception as error:
        print(error)
    finally:
        print("done")
''')
        self.assertIn('catch (error)', generated)
        self.assertIn('finally {', generated)
        self.assertIn('let data;', generated)

    def test_raise_supported(self):
        self.assertIn('throw "no";', self.compile('raise "no"\n'))

    def test_multi_except_unsupported(self):
        with self.assertRaisesRegex(DabbleError, 'one except clause'):
            self.compile('try:\n    pass\nexcept ValueError:\n    pass\nexcept TypeError:\n    pass\n')

    def test_reactive_builtins(self):
        generated = self.compile('a = computed(lambda: 1)\neffect(lambda: print(a.get()))\n')
        self.assertIn('__dabble.computed', generated)
        self.assertIn('__dabble.effect', generated)

    def test_error_contains_source_snippet(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'bad.dab'
            file.write_text('import os\n')
            with self.assertRaises(DabbleError) as cm:
                compile_file(file)
            self.assertIn('import os', str(cm.exception))
            self.assertIn('^', str(cm.exception))

    def test_scaffolding_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'hello'
            new_project(project)
            self.assertTrue((project / 'runtime.js').exists())
            self.assertTrue((project / 'app.dab').exists())
            self.assertIn('window.dabble', (project / 'app.js').read_text())
            targets = build_directory(project)
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0], project / 'app.js')
            with self.assertRaisesRegex(DabbleError, 'directory not empty'):
                new_project(project)

    def test_old_cli_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'app.dab'
            file.write_text('x = signal(1)\n')
            result = subprocess.run([sys.executable, str(ROOT / 'dabble.py'), str(file)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(file.with_suffix('.js').exists())


if __name__ == '__main__':
    unittest.main()
