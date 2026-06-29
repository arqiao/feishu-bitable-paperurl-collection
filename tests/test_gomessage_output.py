import contextlib
import io
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goMessage


class GoMessageOutputTests(unittest.TestCase):
    def test_summary_separates_parse_errors_from_success(self):
        stats = {
            'total': 35,
            'parse_success': 34,
            'parse_errors': 1,
            'csv_written': 35,
            'bitable_written': 30,
            'bitable_failed': 0,
            'bitable_skipped': 4,
            'recall_success': 35,
            'recall_failed': 0,
            'state_advanced': True,
            'state_time': '2026-06-28 15:36:44',
        }

        with contextlib.redirect_stdout(io.StringIO()) as output:
            goMessage.print_run_summary(stats)

        text = output.getvalue()
        self.assertIn('解析成功: 34 条', text)
        self.assertIn('解析异常: 1 条', text)
        self.assertIn('本地 CSV: 35 条', text)
        self.assertIn('多维表格: 成功 30 条，失败 0 条，跳过 4 条', text)
        self.assertIn('撤回消息: 成功 35 条，失败 0 条', text)
        self.assertIn('last_processed_time 已更新: 2026-06-28 15:36:44', text)
        self.assertIn('解析异常已写入异常日志，消息撤回仍按现有策略执行', text)
        self.assertNotIn('\n成功: 35 条', text)

    def test_no_new_messages_reports_state_not_updated(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            goMessage.print_no_new_messages_summary('2026-06-28 15:36:44')

        text = output.getvalue()
        self.assertIn('没有新消息需要处理', text)
        self.assertIn('last_processed_time 未更新', text)
        self.assertIn('2026-06-28 15:36:44', text)


if __name__ == '__main__':
    unittest.main()
