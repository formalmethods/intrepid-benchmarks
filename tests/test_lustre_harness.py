"""
Tests of lustre/harness.py with intrepyd: each of intrepyd's tools, run as
the harness runs it (its worker, in a process of its own), on two small
models written here, one whose property holds and one where it fails.
Skipped without intrepyd.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lustre', 'harness.py')

MODELS = {
    # OK holds: the count never passes 3
    'valid': ('node top(reset: bool) returns (OK: bool);\n'
              'var c: int;\n'
              'let\n'
              '  c = 0 -> (if reset or pre c = 3 then 0 else pre c + 1);\n'
              '  OK = c <= 3;\n'
              'tel\n'),
    # OK fails at the first step where x is true
    'invalid': ('node top(x: bool) returns (OK: bool);\n'
                'let\n'
                '  OK = not x;\n'
                'tel\n'),
}

try:
    from intrepyd.lustre2py import translator as lustre
except ImportError:
    lustre = None


@unittest.skipIf(lustre is None, 'no intrepyd')
class TestIntrepydTools(unittest.TestCase):
    """The worker of each tool gives the verdict of each model"""

    def test_each_tool_on_each_model(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, text in MODELS.items():
                source = os.path.join(folder, name + '.lus')
                with open(source, 'w') as stream:
                    stream.write(text)
                encoding = os.path.join(folder, name + '.py')
                lustre.translate(source, 'top', encoding, 'real', 'int32')
                for tool in ('bmc', 'kind', 'br', 'pdr', 'portfolio'):
                    with self.subTest(model=name, tool=tool):
                        run = subprocess.run([sys.executable, HARNESS, 'worker', tool, encoding,
                                              '--timeout', '5'],
                                             capture_output=True, text=True, timeout=120, cwd=folder)
                        self.assertEqual(0, run.returncode, run.stderr)
                        answer = json.loads(run.stdout.strip().splitlines()[-1])
                        # BMC proves nothing: on a valid model, it can only not know
                        expected = {'invalid': 'Invalid', 'valid': 'Unknown' if tool == 'bmc' else 'Valid'}
                        self.assertEqual(expected[name], answer['verdict'], answer)


if __name__ == '__main__':
    unittest.main()
