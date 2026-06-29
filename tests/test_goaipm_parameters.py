import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goAIPM


def args(**overrides):
    values = {
        'file': None,
        'list': None,
        'daily': None,
        'update': False,
        'weekly': None,
        'towiki': None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class GoAipmParameterTests(unittest.TestCase):
    def test_mode_name_covers_every_primary_mode(self):
        cases = [
            (args(file='url'), '单篇周报'),
            (args(list='list.txt'), '批量周报'),
            (args(daily='url'), '单篇日报'),
            (args(update=True), '日报增量更新'),
            (args(weekly='url'), '周报完善'),
            (args(towiki=['source', 'target']), '写入 Wiki'),
        ]
        for cli_args, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, goAIPM.get_cli_mode(cli_args))

    def test_missing_list_file_fails_preflight(self):
        with self.assertRaisesRegex(ValueError, '列表文件不存在'):
            goAIPM.validate_cli_input(args(list='/tmp/not-found-aipm.txt'))

    def test_missing_local_towiki_source_fails_preflight(self):
        with self.assertRaisesRegex(ValueError, '源文件不存在'):
            goAIPM.validate_cli_input(
                args(towiki=['/tmp/not-found-aipm.pdf', 'wiki-url']))

    def test_existing_list_file_is_normalized(self):
        with tempfile.NamedTemporaryFile() as source_list:
            cli_args = args(list=source_list.name)
            goAIPM.validate_cli_input(cli_args)

        self.assertEqual(os.path.abspath(source_list.name), cli_args.list)

    def test_mode_header_identifies_source(self):
        cli_args = args(daily='https://example.com/daily')

        with contextlib.redirect_stdout(io.StringIO()) as output:
            goAIPM.print_cli_mode(cli_args)

        text = output.getvalue()
        self.assertIn('运行模式: 单篇日报', text)
        self.assertIn('https://example.com/daily', text)


if __name__ == '__main__':
    unittest.main()
