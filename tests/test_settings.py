import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_paid_fallback_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            self.assertFalse(settings.enable_paid_fallback)

    def test_default_model_chain_uses_glm_5_3_for_nvidia(self):
        settings = Settings()
        self.assertEqual(
            settings.llm_models,
            (
                "groq/openai/gpt-oss-120b",
                "nvidia_nim/z-ai/glm-5.3",
                "openai/gpt-5.6-luna",
            ),
        )

    def test_model_chain_can_be_overridden(self):
        with patch.dict(os.environ, {"QA_LLM_MODELS": "groq/a,nvidia_nim/b"}, clear=True):
            settings = Settings.from_env()
            self.assertEqual(settings.llm_models, ("groq/a", "nvidia_nim/b"))

    def test_blank_profile_env_does_not_resolve_to_project_directory(self):
        fake_local = str(Path.cwd() / "fake-local-app-data")
        with patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": fake_local,
                "QA_CHROME_PROFILE_DIR": "",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(
            settings.chrome_profile_dir,
            (Path(fake_local) / "MySANA-QA-Agent" / "browser-profile").resolve(),
        )
        self.assertNotEqual(settings.chrome_profile_dir, Path.cwd().resolve())


if __name__ == "__main__":
    unittest.main()
