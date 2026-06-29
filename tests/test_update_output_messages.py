import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goAIPM
import goWTA


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class UpdateOutputMessageTests(unittest.TestCase):
    def test_aipm_topic_page_reports_retry_recovery(self):
        responses = [
            FakeResponse({'succeeded': False, 'code': 1059, 'msg': '内部错误'}),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]

        with patch.object(goAIPM.ZSXQ_SESSION, 'get', side_effect=responses):
            with patch.object(goAIPM.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = goAIPM.fetch_group_topics_page(
                        'group', 'token', retries=3)

        text = output.getvalue()
        self.assertEqual([], topics)
        self.assertIn('星球帖子列表获取失败（尝试 1/3）', text)
        self.assertIn('内部错误（code=1059）', text)
        self.assertIn('星球帖子列表重试成功（尝试 2/3）', text)

    def test_aipm_find_daily_failure_uses_one_based_page_number(self):
        with patch.object(goAIPM, 'fetch_group_topics_page', return_value=None):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = goAIPM.find_daily_articles_since('group', 'token')

        self.assertIsNone(result)
        self.assertIn('第 1 页最终获取失败，停止查找日报', output.getvalue())
        self.assertNotIn('第 0 页', output.getvalue())

    def test_wta_no_url_message_mentions_state_not_updated(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            goWTA.print_no_urls_update_summary('20260601')

        text = output.getvalue()
        self.assertIn('未提取到任何 URL，结束', text)
        self.assertIn('last_processed_date 未更新', text)
        self.assertIn('本次未提取到待处理 URL', text)
        self.assertIn('20260601', text)


if __name__ == '__main__':
    unittest.main()
