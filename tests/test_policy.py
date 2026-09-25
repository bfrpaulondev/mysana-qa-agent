import unittest

from browser.policy import BrowserPolicy, PolicyViolation


class BrowserPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = BrowserPolicy(("mysana.sanahotels.com",), allow_dangerous_actions=False)

    def test_allows_mysana(self):
        self.policy.ensure_url_allowed("https://mysana.sanahotels.com/programs/test.aspx")

    def test_blocks_external_host(self):
        with self.assertRaises(PolicyViolation):
            self.policy.ensure_url_allowed("https://example.com")

    def test_blocks_dangerous_click(self):
        with self.assertRaises(PolicyViolation):
            self.policy.ensure_click_allowed("Aprovar pagamento")

    def test_allows_regular_click(self):
        self.policy.ensure_click_allowed("Guardar")


if __name__ == "__main__":
    unittest.main()
