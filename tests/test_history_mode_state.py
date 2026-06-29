import contextlib
import io
import os
import sys
import unittest
from unittest.mock import Mock, patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import dfZSXQ
import goWTA
import goWXGZH


class HistoryModeStateTests(unittest.TestCase):
    def test_dfzsxq_history_ignores_marker_and_does_not_advance_state(self):
        group = {
            'name': 'example',
            'group_url': 'https://wx.zsxq.com/group/12345',
            'download_dir': '/tmp/downloads',
            'last_download_date': 1800000000,
        }
        topic = {'topic_ts': 1700000000, 'files': []}

        with patch.object(
            dfZSXQ,
            'fetch_topics_in_range',
            return_value=([topic], True),
        ) as fetch:
            with patch.object(
                dfZSXQ,
                'process_topics',
                return_value=(1, 0, 0, 1700000000),
            ):
                with patch.object(
                    dfZSXQ,
                    'update_group_last_download_marker',
                ) as update_marker:
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        result = dfZSXQ.process_group(
                            group,
                            'token',
                            '20231101',
                            '20231130',
                            '/tmp/config.yaml',
                            advance_state=False,
                            start_ts_exclusive=0,
                        )

        self.assertTrue(result)
        self.assertEqual(0, fetch.call_args.kwargs['start_ts_exclusive'])
        update_marker.assert_not_called()
        self.assertIn('历史模式不推进增量状态', output.getvalue())

    def test_wta_history_does_not_advance_last_processed_date(self):
        client = Mock()
        client.config_path = '/tmp/config.yaml'
        config = {'last_processed_date': '20260601'}

        with patch.object(goWTA, 'set_config_value_preserve_comments') as save:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                changed = goWTA.update_last_processed_state(
                    client,
                    config,
                    target_date='20260620',
                    write_ok=True,
                    advance_state=False,
                )

        self.assertFalse(changed)
        self.assertEqual('20260601', config['last_processed_date'])
        save.assert_not_called()
        self.assertIn('历史模式不推进增量状态', output.getvalue())

    def test_wta_same_date_does_not_report_state_updated(self):
        client = Mock()
        client.config_path = '/tmp/config.yaml'
        config = {'last_processed_date': '20260628'}

        with patch.object(goWTA, 'set_config_value_preserve_comments') as save:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                changed = goWTA.update_last_processed_state(
                    client,
                    config,
                    target_date='20260628',
                    write_ok=True,
                    advance_state=True,
                )

        self.assertFalse(changed)
        save.assert_not_called()
        self.assertIn('没有更晚日期', output.getvalue())

    def test_wxgzh_history_does_not_advance_account_last_update(self):
        account = {'name': 'example', 'last_update': 100}

        with contextlib.redirect_stdout(io.StringIO()) as output:
            changed = goWXGZH.update_account_last_update(
                account, new_timestamp=200, advance_state=False)

        self.assertFalse(changed)
        self.assertEqual(100, account['last_update'])
        self.assertIn('历史模式不推进增量状态', output.getvalue())


if __name__ == '__main__':
    unittest.main()
