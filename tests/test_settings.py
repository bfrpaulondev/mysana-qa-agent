import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_openai_is_primary_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertTrue(settings.prefer_openai)
        self.assertEqual(settings.vision_model, "openai/gpt-5.6-luna")
        self.assertEqual(settings.llm_models[0], "openai/gpt-5.6-luna")

    def test_legacy_env_is_reordered_when_openai_preference_is_on(self):
        with patch.dict(
            os.environ,
            {
                "QA_PREFER_OPENAI": "true",
                "QA_VISION_MODEL": "nvidia_nim/z-ai/glm-5.3-flash",
                "QA_LLM_MODELS": (
                    "groq/openai/gpt-oss-120b,"
                    "nvidia_nim/z-ai/glm-5.3,"
                    "openai/gpt-5.6-luna"
                ),
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.vision_model, "openai/gpt-5.6-luna")
        self.assertEqual(
            settings.llm_models,
            (
                "openai/gpt-5.6-luna",
                "groq/openai/gpt-oss-120b",
                "nvidia_nim/z-ai/glm-5.3",
            ),
        )

    def test_manual_order_is_respected_when_openai_preference_is_off(self):
        with patch.dict(
            os.environ,
            {
                "QA_PREFER_OPENAI": "false",
                "QA_VISION_MODEL": "nvidia_nim/z-ai/glm-5.3-flash",
                "QA_LLM_MODELS": "groq/a,nvidia_nim/b,openai/c",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertFalse(settings.prefer_openai)
        self.assertEqual(settings.vision_model, "nvidia_nim/z-ai/glm-5.3-flash")
        self.assertEqual(settings.llm_models, ("groq/a", "nvidia_nim/b", "openai/c"))

    def test_paid_fallback_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            self.assertFalse(settings.enable_paid_fallback)

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

        self.assertNotEqual(settings.chrome_profile_dir, Path.cwd().resolve())
        if os.name == "nt":
            self.assertEqual(
                settings.chrome_profile_dir,
                (Path(fake_local) / "MySANA-QA-Agent" / "browser-profile").resolve(),
            )
        else:
            self.assertTrue(str(settings.chrome_profile_dir).endswith("runtime/chrome-profile"))


if __name__ == "__main__":
    unittest.main()
