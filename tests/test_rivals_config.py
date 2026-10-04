import os
import unittest
from unittest.mock import patch

from rionnag.integrations.rivals_config import client_options


class RivalsConfigTests(unittest.TestCase):
    def test_browser_fallback_enabled_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(client_options()["use_browser_fallback"])

    def test_conservative_spacing_and_cooldown_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(client_options()["request_interval"], 3)
            self.assertEqual(client_options()["rate_limit_cooldown"], 60)

    def test_spacing_and_cooldown_environment_overrides(self):
        with patch.dict(os.environ, {
            "RIVALS_API_REQUEST_INTERVAL": "5", "RIVALS_API_RATE_LIMIT_COOLDOWN": "120"
        }, clear=True):
            self.assertEqual(client_options()["request_interval"], 5)
            self.assertEqual(client_options()["rate_limit_cooldown"], 120)

    def test_explicit_browser_opt_out_preserved(self):
        with patch.dict(os.environ, {"RIVALS_API_BROWSER_FALLBACK": "false"}, clear=True):
            self.assertFalse(client_options()["use_browser_fallback"])

    def test_explicit_browser_opt_in_preserved(self):
        with patch.dict(os.environ, {"RIVALS_API_BROWSER_FALLBACK": "true"}, clear=True):
            self.assertTrue(client_options()["use_browser_fallback"])
