import os
import sys
import unittest

import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from modules.http_diagnostics import classify_http_failure


class HttpDiagnosticsTests(unittest.TestCase):
    def test_401_is_auth_and_not_retryable(self):
        diag = classify_http_failure(
            status_code=401,
            message='Unauthorized',
            service='知识星球',
            credential_hint='检查 zsxq.access_token',
        )

        self.assertEqual('auth', diag.category)
        self.assertFalse(diag.retryable)
        self.assertIn('知识星球认证失败', diag.summary)
        self.assertIn('zsxq.access_token', diag.action)

    def test_403_is_permission_and_not_retryable(self):
        diag = classify_http_failure(status_code=403, service='飞书')

        self.assertEqual('permission', diag.category)
        self.assertFalse(diag.retryable)
        self.assertIn('飞书访问权限不足', diag.summary)

    def test_429_is_rate_limit_and_retryable(self):
        diag = classify_http_failure(status_code=429, service='微信公众号')

        self.assertEqual('rate_limit', diag.category)
        self.assertTrue(diag.retryable)
        self.assertIn('微信公众号接口限流', diag.summary)

    def test_5xx_is_server_error_and_retryable(self):
        diag = classify_http_failure(status_code=503, message='bad gateway')

        self.assertEqual('server_error', diag.category)
        self.assertTrue(diag.retryable)
        self.assertIn('服务端异常', diag.summary)

    def test_business_5xx_code_is_server_error_and_retryable(self):
        diag = classify_http_failure(
            payload={'code': 500, 'msg': 'Internal Error'},
            service='知识星球',
        )

        self.assertEqual('server_error', diag.category)
        self.assertTrue(diag.retryable)
        self.assertIn('知识星球服务端异常', diag.summary)
        self.assertIn('code=500', diag.detail)

    def test_timeout_exception_is_retryable(self):
        diag = classify_http_failure(
            error=requests.exceptions.ReadTimeout('read timed out'),
            service='飞书',
        )

        self.assertEqual('timeout', diag.category)
        self.assertTrue(diag.retryable)
        self.assertIn('飞书请求超时', diag.summary)

    def test_connection_exception_is_retryable(self):
        diag = classify_http_failure(
            error=requests.exceptions.ConnectionError('connection reset'),
        )

        self.assertEqual('network', diag.category)
        self.assertTrue(diag.retryable)
        self.assertIn('网络连接失败', diag.summary)

    def test_business_code_can_be_classified(self):
        diag = classify_http_failure(
            payload={'code': 1059, 'msg': 'busy'},
            service='知识星球',
        )

        self.assertEqual('temporary', diag.category)
        self.assertTrue(diag.retryable)
        self.assertIn('知识星球接口临时异常', diag.summary)
        self.assertIn('code=1059', diag.detail)


if __name__ == '__main__':
    unittest.main()
