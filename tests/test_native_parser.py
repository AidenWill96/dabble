"""Native frontend tests: lexer, AST shape, diagnostics, precedence, and compatibility."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dabble_syntax as syntax
from dabble import Compiler, DabbleError, compile_file


class NativeParserTests(unittest.TestCase):
    def test_indentation_and_nested_calls(self):
        source = '''def App():
    return div(
        h1("Hi"),
        button("Go", onclick=lambda event: print(event.target.value)),
    )
'''
        kinds = [t.kind for t in syntax.Lexer(source).scan()]
        self.assertEqual(kinds.count("INDENT"), 1)
        self.assertEqual(kinds.count("DEDENT"), 1)
        self.assertEqual(len(syntax.parse(source).body), 1)
        js = Compiler("test.dab").compile(source)
        self.assertIn('"onclick": ((event) => __dabble.print(event.target.value))', js)

    def test_native_ast_is_ours_not_python_ast(self):
        node = syntax.parse("x = 42\n").body[0]
        self.assertIsInstance(node, syntax.Assign)
        self.assertEqual(type(node).__module__, 'dabble_syntax')
        self.assertIsInstance(node.value, syntax.Constant)
        self.assertEqual(node.value.value, 42)
        self.assertEqual(node.col_offset, 0)

    def test_multiline_reactive_dashboard(self):
        source = (ROOT / 'examples' / 'dashboard.dab').read_text()
        js = Compiler("dashboard.dab").compile(source)
        self.assertIn('async function load_sample()', js)
        self.assertIn('__dabble.computed', js)
        self.assertIn('catch (error)', js)

    def test_binding_power_and_parenthesized_expression(self):
        source = 'x = 2 + 3 * 4 ** 2\ny = not x == 12\nz = "yes" if x > 1 else "no"\n'
        js = Compiler("test.dab").compile(source)
        self.assertIn('(2 + (3 * (4 ** 2)))', js)
        self.assertIn('(!__dabble.truthy((x === 12)))', js)
        self.assertIn('(__dabble.truthy((x > 1)) ? "yes" : "no")', js)

    def test_escaped_quotes_and_unicode(self):
        src = 'x = "Hello, \\"world\\"!"\ny = "café"\nz = f"value: {x}"\n'
        js = Compiler('test.dab').compile(src)
        self.assertIn('"café"', js)
        self.assertIn('`value: ${x}`', js)

    def test_reject_inconsistent_indentation(self):
        src = 'def a():\n    x = 1\n  y = 2\n'
        with self.assertRaisesRegex(DabbleError, 'inconsistent indentation'):
            Compiler('bad.dab').compile(src)

    def test_reject_mismatched_brackets(self):
        with self.assertRaisesRegex(DabbleError, 'mismatched closing bracket'):
            Compiler('bad.dab').compile('a = [1, 2)\n')

    def test_reject_tabs(self):
        with self.assertRaisesRegex(DabbleError, 'tabs in indentation'):
            Compiler('bad.dab').compile('def a():\n\treturn 1\n')

    def test_reject_unterminated_string(self):
        with self.assertRaisesRegex(DabbleError, 'unterminated string'):
            Compiler('bad.dab').compile('x = "no\n')

    def test_chained_compare_diagnostics(self):
        with self.assertRaisesRegex(DabbleError, 'chained comparisons'):
            Compiler('bad.dab').compile('if 0 < n < 10:\n    pass\n')

    def test_compiler_cli_tokens_and_ast(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'app.dab'
            f.write_text('n = signal(0)\n', encoding='utf-8')
            t = subprocess.run([sys.executable, str(ROOT / 'dabble.py'), 'tokens', str(f)],
                               capture_output=True, text=True)
            self.assertEqual(t.returncode, 0, t.stderr)
            self.assertIn('NAME', t.stdout)
            p = subprocess.run([sys.executable, str(ROOT / 'dabble.py'), 'ast', str(f)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('Module(body=[Assign(', p.stdout)

    def test_empty_suite_and_source_caret(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'error.dab'
            f.write_text('def hi():\n    pass\n  return 2\n')
            with self.assertRaises(DabbleError) as cm:
                compile_file(f)
            self.assertIn('return 2', str(cm.exception))
            self.assertIn('^', str(cm.exception))


if __name__ == '__main__':
    unittest.main()

class UniqueSyntaxTests(unittest.TestCase):
    def test_component_and_state_are_native_language_constructs(self):
        source = '''component Counter():
    state count = 0
    return p(count)
mount("#app", Counter)
'''
        tree = syntax.parse(source)
        self.assertIsInstance(tree.body[0], syntax.ComponentDef)
        self.assertIsInstance(tree.body[0].body[0], syntax.State)
        generated = Compiler('native.dab').compile(source)
        self.assertIn('function Counter()', generated)
        self.assertIn('count = __dabble.signal(0)', generated)
        self.assertIn('__dabble.mount("#app", Counter)', generated)

    def test_all_examples_compile_natively(self):
        for src in sorted((ROOT / 'examples').glob('*.dab')):
            with self.subTest(example=src.name):
                js = Compiler(str(src)).compile(src.read_text(encoding='utf-8'))
                self.assertIn('window.dabble', js)

class NativeStateSemanticsTests(unittest.TestCase):
    def test_state_is_intrinsic_even_when_signal_name_is_shadowed(self):
        source = """component Counter():
    signal = lambda x: x
    state n = 12
    return p(n)
"""
        output = Compiler('test.dab').compile(source)
        self.assertIn('n = __dabble.signal(12)', output)
        self.assertNotIn('n = signal(12)', output)
