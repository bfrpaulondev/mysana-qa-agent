import unittest

from browser.session import BrowserSession
from qa.openai_computer_runner import OpenAIComputerRunner


class ComputerUseTests(unittest.TestCase):
    def test_screenshot_coordinates_are_scaled_to_css_viewport(self):
        observation = {
            "image_width": 2400,
            "image_height": 1350,
            "viewport_width": 1920,
            "viewport_height": 1080,
        }

        x, y = BrowserSession.scale_computer_point(1200, 675, observation)

        self.assertEqual(x, 960)
        self.assertEqual(y, 540)

    def test_screenshot_coordinates_are_clamped(self):
        observation = {
            "image_width": 1000,
            "image_height": 500,
            "viewport_width": 800,
            "viewport_height": 400,
        }

        x, y = BrowserSession.scale_computer_point(2000, 1000, observation)

        self.assertEqual(x, 799)
        self.assertEqual(y, 399)

    def test_type_action_text_is_redacted_from_report(self):
        safe = OpenAIComputerRunner._safe_action_for_report(
            {"type": "type", "text": "secret-value"}
        )

        self.assertNotIn("text", safe)
        self.assertEqual(safe["text_length"], 12)

    def test_action_label_does_not_expose_typed_text(self):
        label = OpenAIComputerRunner._action_label(
            {"type": "type", "text": "very-secret"}
        )

        self.assertEqual(label, "TYPE — 11 caracteres")
        self.assertNotIn("very-secret", label)


if __name__ == "__main__":
    unittest.main()
