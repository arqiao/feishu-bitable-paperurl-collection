import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feishu_client import FeishuClient


class FakeResponse:
    def json(self):
        return {
            "code": 99991663,
            "msg": "invalid refresh token",
        }


class FakeSession:
    def __init__(self):
        self.post_kwargs = None

    def post(self, url, **kwargs):
        self.post_kwargs = kwargs
        return FakeResponse()


class FeishuClientTokenTest(unittest.TestCase):
    def make_client(self, credentials):
        with patch.object(FeishuClient, "_load_yaml", return_value={}):
            with patch("feishu_client._secrets_load", return_value=credentials):
                return FeishuClient()

    def test_check_token_valid_returns_false_when_project_token_missing(self):
        client = self.make_client({"feishu": {"app_id": "app", "app_secret": "secret"}})

        self.assertFalse(client.check_token_valid())

    def test_refresh_access_token_sets_timeout_on_post(self):
        credentials = {
            "feishu": {"app_id": "app", "app_secret": "secret"},
            "auth_feishuMSG-xls": {"user_refresh_token": "refresh"},
        }
        client = self.make_client(credentials)
        fake_session = FakeSession()
        client._session = fake_session

        with redirect_stdout(StringIO()):
            self.assertFalse(client.refresh_access_token())

        self.assertEqual(30, fake_session.post_kwargs.get("timeout"))

    def test_refresh_access_token_returns_false_when_project_token_missing(self):
        client = self.make_client({"feishu": {"app_id": "app", "app_secret": "secret"}})

        with redirect_stdout(StringIO()):
            self.assertFalse(client.refresh_access_token())

    def test_message_api_failure_is_not_returned_as_empty_success(self):
        client = self.make_client({
            "feishu": {"app_id": "app", "app_secret": "secret"},
        })
        response = Mock()
        response.json.return_value = {
            "code": 99991672,
            "msg": "no permission",
        }
        client._session = Mock()
        client._session.get.return_value = response

        with patch.object(
            client, "_get_tenant_access_token", return_value="tenant-token"
        ):
            with redirect_stdout(StringIO()):
                result = client.get_chat_messages("chat-id")

        self.assertIsNone(result)
        self.assertIn("no permission", client.last_error)


if __name__ == "__main__":
    unittest.main()
