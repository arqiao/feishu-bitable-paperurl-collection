import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import goWXGZH


class ChromeCookieTest(unittest.TestCase):
    def test_strip_host_digest_removes_chrome_cookie_prefix(self):
        host_key = "mp.weixin.qq.com"
        value = b"slave_sid=abc123"
        encrypted_plaintext = hashlib.sha256(host_key.encode("utf-8")).digest() + value

        self.assertEqual(
            value,
            goWXGZH._strip_chrome_cookie_host_digest(encrypted_plaintext, host_key),
        )


if __name__ == "__main__":
    unittest.main()
