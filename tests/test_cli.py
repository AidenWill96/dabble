"""Behavioral tests for the dabble run/dev command surface."""
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(ROOT / 'dabble.py')]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextmanager
def start_server(command, project, port):
    with tempfile.TemporaryFile(mode='w+t') as logs:
        proc = subprocess.Popen(CLI + [command, str(project), '--port', str(port)],
                                stdout=logs, stderr=subprocess.STDOUT)
        try:
            url = f'http://127.0.0.1:{port}/index.html'
            for _ in range(50):
                if proc.poll() is not None:
                    logs.seek(0)
                    raise AssertionError(f'dabble {command} exited early:\n{logs.read()}')
                try:
                    with urlopen(url, timeout=0.4) as result:
                        if result.status == 200:
                            break
                except (OSError, URLError):
                    time.sleep(0.08)
            else:
                logs.seek(0)
                raise AssertionError(f'dabble {command} did not start:\n{logs.read()}')
            yield f'http://127.0.0.1:{port}'
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


class CLITests(unittest.TestCase):
    def make_site(self, directory):
        site = directory / 'my-site'
        result = subprocess.run(CLI + ['new', str(site)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((site / 'runtime.js').is_file())
        self.assertTrue((site / 'app.js').is_file())
        self.assertTrue((site / 'styles.css').is_file())
        self.assertIn('href="styles.css"', (site / 'index.html').read_text())
        return site

    def test_version_and_help(self):
        version = subprocess.run(CLI + ['--version'], capture_output=True, text=True)
        self.assertEqual(version.returncode, 0)
        self.assertEqual(version.stdout.strip(), 'dabble 0.5.0')
        for command in ('run', 'dev', 'compile', 'new', 'build'):
            self.assertIn(command, subprocess.run(CLI + ['--help'], capture_output=True,
                                                  text=True).stdout)

    def test_build_and_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self.make_site(Path(tmp))
            (site / 'app.js').unlink()
            result = subprocess.run(CLI + ['build'], cwd=site, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Compiled 1 dabble file(s)', result.stdout)
            self.assertTrue((site / 'app.js').exists())
            target = site / 'other.js'
            result = subprocess.run(CLI + ['compile', str(site / 'app.dab'), '-o', str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.is_file())

    def test_run_builds_once_and_serves(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self.make_site(Path(tmp))
            with start_server('run', site, free_port()) as origin:
                page = urlopen(origin).read().decode()
                self.assertIn('<div id="app"></div>', page)
                before = urlopen(origin + '/app.js').read().decode()
                self.assertIn('Hello from dabble', before)
                original = (site / 'app.dab').read_text()
                (site / 'app.dab').write_text(original.replace('Hello from dabble', 'Changed in source'))
                after = urlopen(origin + '/app.js').read().decode()
                self.assertEqual(before, after, 'run must build once, not rebuild on requests')

    def test_dev_rebuilds_source_and_reports_syntax_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self.make_site(Path(tmp))
            with start_server('dev', site, free_port()) as origin:
                before = urlopen(origin + '/app.js').read().decode()
                self.assertIn('Hello from dabble', before)
                original = (site / 'app.dab').read_text()
                (site / 'app.dab').write_text(original.replace('Hello from dabble', 'Changed in dev'))
                after = urlopen(origin + '/app.js').read().decode()
                self.assertIn('Changed in dev', after)
                self.assertNotEqual(before, after)
                # HTML fallback for browser-history/client-side routes.
                route = Request(origin + '/about', headers={'Accept': 'text/html'})
                self.assertIn('<div id="app"></div>', urlopen(route).read().decode())
                (site / 'app.dab').write_text('component Broken():\n  state =\n')
                with self.assertRaises(HTTPError) as exc:
                    urlopen(origin + '/app.js')
                self.assertEqual(exc.exception.code, 500)
                self.assertIn('compilation failed', exc.exception.read().decode())

    def test_dev_generates_missing_assets_without_overwriting_styles(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / 'minimal'
            site.mkdir()
            (site / 'app.dab').write_text('mount("#app", lambda: h1("Minimal"))\n')
            with start_server('dev', site, free_port()) as origin:
                self.assertIn('href="styles.css"', urlopen(origin + '/').read().decode())
                self.assertIn('starter CSS', urlopen(origin + '/styles.css').read().decode())
                self.assertTrue((site / 'runtime.js').exists())
            (site / 'styles.css').write_text('body { color: rebeccapurple; }')
            with start_server('dev', site, free_port()) as origin:
                self.assertEqual(urlopen(origin + '/styles.css').read().decode(),
                                 'body { color: rebeccapurple; }')

    def test_dev_migrates_only_default_v04_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self.make_site(Path(tmp))
            from dabble import V04_INLINE_STYLE
            original = (site / 'index.html').read_text()
            old_html = original.replace('<link rel="stylesheet" href="styles.css">',
                                        V04_INLINE_STYLE)
            (site / 'index.html').write_text(old_html)
            (site / 'styles.css').unlink()
            with start_server('dev', site, free_port()) as origin:
                new_html = urlopen(origin).read().decode()
                self.assertNotIn(V04_INLINE_STYLE, new_html)
                self.assertIn('href="styles.css"', new_html)
                self.assertTrue((site / 'styles.css').exists())

    def test_dev_preserves_custom_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = self.make_site(Path(tmp))
            html = '<!doctype html><div id="app"></div><script src="runtime.js"></script><script src="app.js"></script>'
            (site / 'index.html').write_text(html)
            with start_server('dev', site, free_port()) as origin:
                self.assertEqual(urlopen(origin).read().decode(), html)

    def test_invalid_port(self):
        result = subprocess.run(CLI + ['run', '--port', '99999'], capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('--port must be between', result.stderr)


if __name__ == '__main__':
    unittest.main()
