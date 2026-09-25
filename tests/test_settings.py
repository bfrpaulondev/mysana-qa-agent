import os
import unittest
from unittest.mock import patch

from core.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_paid_fallback_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            self.assertFalse(settings.enable_paid_fallback)

    def test_model_chain_can_be_overridden(self):
        with patch.dict(os.environ, {"QA_LLM_MODELS": "groq/a,nvidia_nim/b"}, clear=True):
            settings = Settings.from_env()
            self.assertEqual(settings.llm_models, ("groq/a", "nvidia_nim/b"))


if __name__ == "__main__":
    unittest.main()
