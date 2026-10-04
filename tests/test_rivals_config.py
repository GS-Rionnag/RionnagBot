import os
import unittest
from unittest.mock import patch

from rionnag.integrations.rivals_config import client_options


class RivalsConfigTests(unittest.TestCase):
    def test_browser_fallback_enabled_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(client_options()["use_browser_fallback"])

    def test_explicit_browser_opt_out_preserved(self):
        with patch.dict(os.environ, {"RIVALS_API_BROWSER_FALLBACK": "false"}, clear=True):
            self.assertFalse(client_options()["use_browser_fallback"])

    def test_explicit_browser_opt_in_preserved(self):
        with patch.dict(os.environ, {"RIVALS_API_BROWSER_FALLBACK": "true"}, clear=True):
            self.assertTrue(client_options()["use_browser_fallback"])
