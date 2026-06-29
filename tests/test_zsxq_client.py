import os
import sys
import unittest
from unittest.mock import Mock

import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from modules.zsxq_client import (
    ZSXQ_CREDENTIAL_HINT,
    fetch_zsxq_json_with_retry,
    zsxq_headers,
)


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class ZsxqClientTests(unittest.TestCase):
    def test_headers_include_access_token_cookie(self):
        headers = zsxq_headers('abc123')

        self.assertEqual('zsxq_access_token=abc123', headers['Cookie'])
        self.assertEqual('https://wx.zsxq.com/', headers['Referer'])

    def test_auth_failure_stops_without_retry(self):
        session = Mock()
        session.get.return_value = FakeResponse({
            'succeeded': False,
            'code': 401,
            'msg': 'Unauthorized',
        })
        printer = Mock()
        sleeper = Mock()

        result = fetch_zsxq_json_with_retry(
            session, 'https://example.test', 'token', '获取帖子列表',
            sleep_func=sleeper, printer=printer)

        self.assertIsNone(result)
        self.assertEqual(1, session.get.call_count)
        sleeper.assert_not_called()
        printed = '\n'.join(call.args[0] for call in printer.call_args_list)
        self.assertIn('知识星球认证失败', printed)
        self.assertIn(ZSXQ_CREDENTIAL_HINT, printed)

    def test_rate_limit_retries_and_reports_recovery(self):
        session = Mock()
        session.get.side_effect = [
            FakeResponse({
                'succeeded': False,
                'code': 429,
                'msg': 'Too Many Requests',
            }),
            FakeResponse({'succeeded': True, 'resp_data': {'topics': []}}),
        ]
        printer = Mock()
        sleeper = Mock()

        result = fetch_zsxq_json_with_retry(
            session, 'https://example.test', 'token', '获取帖子列表',
            sleep_func=sleeper, printer=printer)

        self.assertEqual({'succeeded': True, 'resp_data': {'topics': []}},
                         result)
        self.assertEqual(2, session.get.call_count)
        sleeper.assert_called_once_with(2)
        printed = '\n'.join(call.args[0] for call in printer.call_args_list)
        self.assertIn('知识星球接口限流', printed)
        self.assertIn('获取帖子列表重试成功（尝试 2/5）', printed)

    def test_timeout_retries(self):
        session = Mock()
        session.get.side_effect = [
            requests.exceptions.ReadTimeout('read timed out'),
            FakeResponse({'succeeded': True, 'resp_data': {}}),
        ]
        sleeper = Mock()

        result = fetch_zsxq_json_with_retry(
            session, 'https://example.test', 'token', '获取下载链接',
            sleep_func=sleeper, printer=Mock())

        self.assertEqual({'succeeded': True, 'resp_data': {}}, result)
        sleeper.assert_called_once_with(2)


if __name__ == '__main__':
    unittest.main()
