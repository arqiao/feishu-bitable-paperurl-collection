import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import goAIPM


class GoAipmCredentialsTest(unittest.TestCase):
    def test_zsxq_auth_error_points_to_central_secrets(self):
        with patch.object(
            goAIPM,
            "fetch_group_topics_page",
            side_effect=goAIPM.ZsxqAuthError("HTTP 401 Unauthorized"),
        ):
            output = StringIO()
            with redirect_stdout(output):
                result = goAIPM.find_daily_articles_since(
                    "group-id", "token"
                )

        self.assertIsNone(result)
        self.assertIn("~/.config/secrets/gtokens.yaml", output.getvalue())
        self.assertNotIn("cfg/credentials.yaml", output.getvalue())


if __name__ == "__main__":
    unittest.main()
