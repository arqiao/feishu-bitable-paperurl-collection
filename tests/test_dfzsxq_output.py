import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import dfZSXQ


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class DfZsxqOutputTests(unittest.TestCase):
    def test_topics_page_reports_recovery_after_retry(self):
        responses = [
            FakeResponse({'succeeded': False, 'code': 500, 'msg': '暂时不可用'}),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', side_effect=responses):
            with patch.object(dfZSXQ.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        self.assertEqual([], topics)
        self.assertIn('获取帖子列表失败（尝试 1/5）', output.getvalue())
        self.assertIn('获取帖子列表重试成功（尝试 2/5）', output.getvalue())

    def test_download_url_reports_recovery_after_retry(self):
        responses = [
            FakeResponse({'succeeded': False, 'code': 500, 'msg': '暂时不可用'}),
            FakeResponse({
                'succeeded': True,
                'resp_data': {'download_url': 'https://example.com/file'},
            }),
        ]

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', side_effect=responses):
            with patch.object(dfZSXQ.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    url = dfZSXQ.get_download_url('file', 'token')

        self.assertEqual('https://example.com/file', url)
        self.assertIn('获取下载链接失败（尝试 1/5）', output.getvalue())
        self.assertIn('获取下载链接重试成功（尝试 2/5）', output.getvalue())

    def test_group_fetch_failure_is_not_reported_as_empty_success(self):
        group = {
            'name': '测试群组',
            'group_url': 'https://wx.zsxq.com/group/123',
            'download_dir': '/tmp/downloads',
            'last_download_date': 1777982400,
        }

        with patch.object(dfZSXQ, 'fetch_topics_page', return_value=None):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                ok = dfZSXQ.process_group(
                    group, 'token', '20260505', '20260506', '/tmp/config.yaml')

        text = output.getvalue()
        self.assertFalse(ok)
        self.assertIn('第 1 页最终获取失败，抓取中止', text)
        self.assertIn('本群组处理失败：帖子列表未完整获取', text)
        self.assertNotIn('翻页完成', text)
        self.assertNotIn('无文件需要下载', text)

    def test_missing_cursor_is_reported_as_incomplete_fetch(self):
        topics = [{
            'topic_id': 1,
            'create_time': '',
            'talk': {'files': []},
        }]

        with patch.object(dfZSXQ, 'fetch_topics_page', return_value=topics):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                _, completed = dfZSXQ.fetch_topics_in_range(
                    'group', 'token', '20260505', '20260506')

        self.assertFalse(completed)
        self.assertIn('缺少分页游标，抓取中止', output.getvalue())
        self.assertNotIn('翻页完成', output.getvalue())

    def test_later_page_failure_starts_on_a_new_terminal_line(self):
        first_page = [{
            'topic_id': 1,
            'create_time': '2026-05-06T12:00:00+08:00',
            'talk': {'files': []},
        }]

        with patch.object(
                dfZSXQ, 'fetch_topics_page', side_effect=[first_page, None]):
            with patch.object(dfZSXQ.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    dfZSXQ.fetch_topics_in_range(
                        'group', 'token', '20260505', '20260506')

        self.assertIn('含文件帖子\n  第 2 页最终获取失败', output.getvalue())

    def test_page_progress_includes_files_found_on_current_page(self):
        topics = [{
            'topic_id': 1,
            'create_time': '2026-05-06T12:00:00+08:00',
            'talk': {'files': [{'file_id': 1, 'name': 'report.pdf'}]},
        }]

        with patch.object(dfZSXQ, 'fetch_topics_page', side_effect=[topics, []]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                dfZSXQ.fetch_topics_in_range(
                    'group', 'token', '20260506', '20260506',
                    start_ts_exclusive=1777982400)

        self.assertIn('已找到 1 个含文件帖子', output.getvalue())

    def test_topics_page_auth_failure_stops_without_retry(self):
        response = FakeResponse({
            'succeeded': False,
            'code': 401,
            'msg': 'Unauthorized',
        })

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', return_value=response) as get:
            with patch.object(dfZSXQ.time, 'sleep') as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        text = output.getvalue()
        self.assertIsNone(topics)
        self.assertEqual(1, get.call_count)
        sleep.assert_not_called()
        self.assertIn('知识星球认证失败', text)
        self.assertIn('~/.config/secrets/gtokens.yaml', text)
        self.assertIn('zsxq.access_token', text)

    def test_topics_page_permission_failure_stops_without_retry(self):
        response = FakeResponse({
            'succeeded': False,
            'code': 403,
            'msg': 'Forbidden',
        })

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', return_value=response) as get:
            with patch.object(dfZSXQ.time, 'sleep') as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        text = output.getvalue()
        self.assertIsNone(topics)
        self.assertEqual(1, get.call_count)
        sleep.assert_not_called()
        self.assertIn('知识星球访问权限不足', text)
        self.assertIn('星球成员权限', text)

    def test_topics_page_rate_limit_is_retryable(self):
        responses = [
            FakeResponse({'succeeded': False, 'code': 429, 'msg': 'Too Many Requests'}),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', side_effect=responses) as get:
            with patch.object(dfZSXQ.time, 'sleep') as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        self.assertEqual([], topics)
        self.assertEqual(2, get.call_count)
        sleep.assert_called_once()
        self.assertIn('知识星球接口限流', output.getvalue())
        self.assertIn('重试成功', output.getvalue())

    def test_topics_page_zsxq_1059_is_reported_as_temporary_error(self):
        responses = [
            FakeResponse({'succeeded': False, 'code': 1059}),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', side_effect=responses):
            with patch.object(dfZSXQ.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        self.assertEqual([], topics)
        self.assertIn('知识星球接口临时异常', output.getvalue())
        self.assertIn('code=1059', output.getvalue())
        self.assertIn('重试成功', output.getvalue())

    def test_topics_page_server_error_is_retryable(self):
        responses = [
            FakeResponse({'succeeded': False, 'code': 500, 'msg': 'Internal Error'}),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', side_effect=responses) as get:
            with patch.object(dfZSXQ.time, 'sleep') as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        self.assertEqual([], topics)
        self.assertEqual(2, get.call_count)
        sleep.assert_called_once()
        self.assertIn('知识星球服务端异常', output.getvalue())

    def test_topics_page_network_error_is_retryable(self):
        responses = [
            dfZSXQ.requests.Timeout('timed out'),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', side_effect=responses) as get:
            with patch.object(dfZSXQ.time, 'sleep') as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    topics = dfZSXQ.fetch_topics_page('group', 'token')

        self.assertEqual([], topics)
        self.assertEqual(2, get.call_count)
        sleep.assert_called_once()
        self.assertIn('请求超时', output.getvalue())

    def test_download_url_auth_failure_stops_without_retry(self):
        response = FakeResponse({
            'succeeded': False,
            'code': 401,
            'msg': 'Unauthorized',
        })

        with patch.object(dfZSXQ.ZSXQ_SESSION, 'get', return_value=response) as get:
            with patch.object(dfZSXQ.time, 'sleep') as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    url = dfZSXQ.get_download_url('file', 'token')

        self.assertIsNone(url)
        self.assertEqual(1, get.call_count)
        sleep.assert_not_called()
        self.assertIn('知识星球认证失败', output.getvalue())


if __name__ == '__main__':
    unittest.main()
