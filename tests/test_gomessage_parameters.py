import argparse
import contextlib
import io
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goMessage


def args(**overrides):
    values = {
        'all': False,
        'reset': False,
        'start': None,
        'end': None,
        'list_nolink': False,
        'profile': 'ai',
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class GoMessageParameterTests(unittest.TestCase):
    def test_reset_and_all_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, '--reset.*--all'):
            goMessage.validate_cli_args(args(reset=True, all=True))

    def test_list_nolink_rejects_state_mutating_or_range_options(self):
        for option in (
            {'reset': True},
            {'start': 1},
            {'end': 2},
        ):
            with self.subTest(option=option):
                with self.assertRaisesRegex(ValueError, '--list-nolink'):
                    goMessage.validate_cli_args(
                        args(list_nolink=True, **option))

    def test_range_indices_must_be_positive_and_ordered(self):
        invalid = [
            args(start=0),
            args(end=0),
            args(start=3, end=2),
        ]
        for cli_args in invalid:
            with self.subTest(cli_args=cli_args):
                with self.assertRaises(ValueError):
                    goMessage.validate_cli_args(cli_args)

    def test_reset_changes_fetch_start_without_persisting_zero(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            effective_start = goMessage.resolve_fetch_start(
                args(reset=True), configured_last_processed_time=123456)

        self.assertEqual(0, effective_start)
        self.assertIn('成功完成后覆盖', output.getvalue())
        self.assertNotIn('已重置处理时间', output.getvalue())

    def test_all_and_read_only_list_modes_are_valid(self):
        goMessage.validate_cli_args(args(all=True))
        goMessage.validate_cli_args(args(all=True, list_nolink=True))
        goMessage.validate_cli_args(args(list_nolink=True))

    def test_partial_range_does_not_advance_state(self):
        advance, reason = goMessage.get_state_advance_decision(
            has_urls=True,
            bitable_write_ok=True,
            has_bitable_rows=True,
            partial_range=True,
        )

        self.assertFalse(advance)
        self.assertIn('部分范围', reason)


if __name__ == '__main__':
    unittest.main()
