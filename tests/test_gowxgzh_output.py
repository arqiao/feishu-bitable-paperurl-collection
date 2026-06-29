import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goWXGZH


class FakeParser:
    def __init__(self, results):
        self.results = list(results)

    def parse_url(self, _url):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class GoWxgzhOutputTests(unittest.TestCase):
    def test_parse_articles_reports_retry_reason_and_recovery(self):
        parser = FakeParser([
            {
                'url': 'https://mp.weixin.qq.com/s/test',
                'title': '',
                'source': '',
                'publish_date': '',
                'weekday': '',
                'error_info': '临时网络错误',
            },
            {
                'url': 'https://mp.weixin.qq.com/s/test',
                'title': '测试文章',
                'source': '微信-测试号',
                'publish_date': '20260628',
                'weekday': '周日',
                'error_info': '',
            },
        ])
        articles = [{
            'url': 'https://mp.weixin.qq.com/s/test',
            'title': '测试文章',
            'publish_ts': 1782660000,
        }]

        with patch.object(goWXGZH.time, 'sleep'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                parsed_rows, error_rows = goWXGZH.parse_articles(
                    parser, articles, '测试号')

        text = output.getvalue()
        self.assertEqual(1, len(parsed_rows))
        self.assertEqual([], error_rows)
        self.assertIn('解析失败（尝试 1/3）：临时网络错误；3 秒后重试', text)
        self.assertIn('解析重试成功（尝试 2/3）', text)

    def test_fetch_articles_reports_incomplete_fetch(self):
        with patch.object(goWXGZH, '_wx_publish_request', return_value=None):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                articles, error_rows = goWXGZH.fetch_articles(
                    'biz', 'cookie', 'token', 1, 2, '测试号')

        text = output.getvalue()
        self.assertEqual([], articles)
        self.assertEqual(1, len(error_rows))
        self.assertIn('第 1 页最终获取失败，文章列表抓取中止', text)
        self.assertIn('本公众号文章列表未完整获取', text)

    def test_print_no_articles_summary_distinguishes_fetch_error(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            goWXGZH.print_no_articles_summary(
                [{'错误类型': '抓取失败'}], 123)

        text = output.getvalue()
        self.assertIn('未抓取到文章', text)
        self.assertIn('原因：文章列表抓取失败', text)
        self.assertIn('last_update 未更新', text)


if __name__ == '__main__':
    unittest.main()
