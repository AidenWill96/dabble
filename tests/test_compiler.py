"""Run with: python -m unittest discover -s tests -v"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dabble import Compiler, DabbleError


class CompilerTests(unittest.TestCase):
    def compile(self, code):
        return Compiler("test.dab").compile(code)

    def test_event_handler_and_state(self):
        output = self.compile('''\ndef App():
    n = signal(0)
    def click():
        n.set(n.get() + 1)
    return button("+", onclick=click)
mount("#app", App)
''')
        self.assertIn('function App()', output)
        self.assertIn('let n;', output)
        self.assertIn('__dabble.signal(0)', output)
        self.assertIn('"onclick": click', output)
        self.assertIn('__dabble.mount("#app", App)', output)

    def test_control_flow_scope_and_literals(self):
        output = self.compile('''\ndef names():
    found = []
    for i in range(3):
        if i > 0:
            found = ["a", "b"]
    return found
''')
        self.assertIn('let found, i;', output)
        self.assertIn('for (i of __dabble.range(3))', output)
        self.assertIn('if (__dabble.truthy((i > 0)))', output)

    def test_lambda_fstrings_and_properties(self):
        output = self.compile('''\ndef App():
    name = "Ada"
    return div(f"Hi {name}", button("Go", onclick=lambda event: print(event.target.value)))
''')
        self.assertIn('`Hi ${name}`', output)
        self.assertIn('((event) => __dabble.print(event.target.value))', output)

    def test_invalid_import_gets_located_error(self):
        with self.assertRaisesRegex(DabbleError, 'test.dab:1:1: unresolved module import'):
            self.compile('import os\n')

    def test_bad_assignment(self):
        with self.assertRaisesRegex(DabbleError, 'assignment requires one plain variable name'):
            self.compile('x.y = 4\n')

    def test_bad_kwargs(self):
        with self.assertRaisesRegex(DabbleError, 'keyword properties are only supported'):
            self.compile('def f(x):\n    return x\nf(x=3)\n')

    def test_return_outside_function(self):
        with self.assertRaisesRegex(DabbleError, 'return is only valid inside functions'):
            self.compile('return 5\n')

    def test_break_outside_loop(self):
        with self.assertRaisesRegex(DabbleError, 'break is only valid inside loops'):
            self.compile('break\n')

    def test_unsupported_class(self):
        with self.assertRaisesRegex(DabbleError, 'unsupported statement: ClassDef'):
            self.compile('class Thing:\n    pass\n')

    def test_empty_program(self):
        self.assertIn('window.dabble', self.compile('# Empty\n'))


if __name__ == '__main__':
    unittest.main()
