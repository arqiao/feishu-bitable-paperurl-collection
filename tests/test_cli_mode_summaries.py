import contextlib
import io
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import dfZSXQ
import goWTA
import goWXGZH


class CliModeSummaryTests(unittest.TestCase):
    def test_dfzsxq_summary_reports_mode_and_group_results(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            dfZSXQ.print_run_summary(
                mode='历史下载', total=2, succeeded=1, failed=1, skipped=0)

        text = output.getvalue()
        self.assertIn('运行模式: 历史下载', text)
        self.assertIn('群组: 总计 2，成功 1，失败 1，跳过 0', text)
        self.assertIn('运行结果: 部分失败', text)

    def test_wta_history_summary_reports_state_not_updated(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            goWTA.print_run_summary(
                extracted=10,
                parsed=9,
                errors=1,
                state_changed=False,
                state_reason='历史模式不推进增量状态',
            )

        text = output.getvalue()
        self.assertIn('状态推进: 未更新', text)
        self.assertIn('历史模式不推进增量状态', text)

    def test_wxgzh_summary_reports_errors_and_state_updates(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            goWXGZH.print_run_summary(
                mode='增量更新',
                accounts=3,
                fetched=8,
                written=7,
                errors=1,
                state_updates=2,
            )

        text = output.getvalue()
        self.assertIn('运行模式: 增量更新', text)
        self.assertIn('公众号: 3 个', text)
        self.assertIn('抓取: 8 篇，写入: 7 条，异常: 1 条', text)
        self.assertIn('last_update 更新: 2 个公众号', text)


if __name__ == '__main__':
    unittest.main()
