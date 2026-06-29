import os
import subprocess
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
SCRIPTS = [
    'src/dfZSXQ.py',
    'src/goWTA.py',
    'src/goWXGZH.py',
    'src/goAIPM.py',
    'src/goMessage.py',
]


def run_script(*arguments):
    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.join(PROJECT_ROOT, 'src')
    return subprocess.run(
        [PYTHON, *arguments],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


class CliHelpTests(unittest.TestCase):
    def test_help_is_available_for_all_scripts(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = run_script(script, '--help')
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn('usage:', result.stdout.lower())
                self.assertNotIn('寮€', result.stdout)

    def test_invalid_date_ranges_fail_before_runtime(self):
        commands = [
            ('src/dfZSXQ.py', '--his', '20260230', '20260301'),
            ('src/goWTA.py', '--his', '20260602', '20260601'),
            ('src/goWXGZH.py', '--his', 'bad', '20260601'),
        ]
        for command in commands:
            with self.subTest(command=command):
                result = run_script(*command)
                self.assertEqual(2, result.returncode)
                self.assertIn('error:', result.stderr.lower())
                self.assertNotIn('Token', result.stdout)

    def test_invalid_modifier_combinations_fail_before_runtime(self):
        commands = [
            ('src/goMessage.py', '--reset', '--all'),
            ('src/goMessage.py', '--list-nolink', '--start', '1'),
            ('src/goWXGZH.py', '--searchbiz', 'example', '--refresh-cache'),
            ('src/goAIPM.py', '--list', '/tmp/not-found-aipm-list.txt'),
        ]
        for command in commands:
            with self.subTest(command=command):
                result = run_script(*command)
                self.assertEqual(2, result.returncode)
                self.assertIn('error:', result.stderr.lower())
                self.assertNotIn('Token', result.stdout)


if __name__ == '__main__':
    unittest.main()
