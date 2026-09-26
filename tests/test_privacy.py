from __future__ import annotations

import unittest

from output.markdown import render_record_markdown
from privacy import redact_sensitive


class PrivacyTests(unittest.TestCase):
    def test_public_projection_redacts_accounts_and_phone_numbers(self) -> None:
        value = "银行账号：270-26262-1 联系电话：13800138000"
        cleaned = redact_sensitive(value)
        self.assertNotIn("270-26262-1", cleaned)
        self.assertNotIn("13800138000", cleaned)
        markdown = render_record_markdown({"title": "通知", "content": value, "key_points": [value]})
        self.assertNotIn("270-26262-1", markdown)
        self.assertNotIn("13800138000", markdown)


if __name__ == "__main__":
    unittest.main()
