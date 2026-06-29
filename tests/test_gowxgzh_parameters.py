import argparse
import contextlib
import inspect
import io
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goWXGZH


def args(**overrides):
    values = {
        'his': None,
        'update': False,
        'searchbiz': None,
        'list': None,
        'refresh_cache': False,
        'repair_last_update': False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class GoWxgzhParameterTests(unittest.TestCase):
    def test_refresh_cache_requires_history_or_update_mode(self):
        invalid = [
            args(searchbiz='example', refresh_cache=True),
            args(repair_last_update=True, refresh_cache=True),
        ]
        for cli_args in invalid:
            with self.subTest(cli_args=cli_args):
                with self.assertRaisesRegex(ValueError, '--refresh-cache'):
                    goWXGZH.validate_cli_args(cli_args)

    def test_search_rejects_account_list_modifier(self):
        with self.assertRaisesRegex(ValueError, '--list'):
            goWXGZH.validate_cli_args(
                args(searchbiz='example', list='/tmp/accounts.yaml'))

    def test_account_list_and_refresh_cache_are_accepted_in_data_modes(self):
        valid = [
            args(update=True, list='/tmp/accounts.yaml'),
            args(update=True, refresh_cache=True),
            args(
                his=['20260601', '20260602'],
                list='/tmp/accounts.yaml',
                refresh_cache=True,
            ),
            args(repair_last_update=True, list='/tmp/accounts.yaml'),
        ]
        for cli_args in valid:
            with self.subTest(cli_args=cli_args):
                goWXGZH.validate_cli_args(cli_args)

    def test_mode_name_covers_every_primary_mode(self):
        cases = [
            (args(his=['20260601', '20260602']), '历史批量'),
            (args(update=True), '增量更新'),
            (args(searchbiz='example'), '搜索公众号'),
            (args(repair_last_update=True), '修复 last_update'),
        ]
        for cli_args, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, goWXGZH.get_cli_mode(cli_args))

    def test_source_has_no_duplicate_mojibake_repair_branch(self):
        source = inspect.getsource(goWXGZH.main)
        self.assertNotIn('寮€濮嬩慨澶', source)
        self.assertNotIn('宸蹭慨澶', source)

    def test_backend_token_ready_message_does_not_expose_token(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            goWXGZH.print_backend_token_ready()

        self.assertIn('后台 token 获取成功', output.getvalue())
        self.assertNotIn('secret-token', output.getvalue())


if __name__ == '__main__':
    unittest.main()
