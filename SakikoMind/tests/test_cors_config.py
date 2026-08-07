import os
import unittest

from api.main import _cors_allowed_origins


class CorsConfigTests(unittest.TestCase):
    def setUp(self):
        self.previous_value = os.environ.get("CORS_ALLOWED_ORIGINS")

    def tearDown(self):
        if self.previous_value is None:
            os.environ.pop("CORS_ALLOWED_ORIGINS", None)
        else:
            os.environ["CORS_ALLOWED_ORIGINS"] = self.previous_value

    def test_splits_and_strips_configured_origins(self):
        os.environ["CORS_ALLOWED_ORIGINS"] = " https://app.example.com, https://admin.example.com "

        self.assertEqual(
            _cors_allowed_origins(),
            ["https://app.example.com", "https://admin.example.com"],
        )

    def test_empty_config_falls_back_to_wildcard_for_local_development(self):
        os.environ["CORS_ALLOWED_ORIGINS"] = " , "

        self.assertEqual(_cors_allowed_origins(), ["*"])


if __name__ == "__main__":
    unittest.main()
