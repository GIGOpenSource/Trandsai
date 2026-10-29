"""消息策略配图：编码/历史标注。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.strategy_image import (  # noqa: E402
    STRATEGY_IMAGE_PREFIX,
    annotate_chat_message,
    assistant_message_payload,
    decode_strategy_image_url,
    encode_strategy_image_content,
    is_strategy_image_content,
    normalize_strategy_image_url,
)


class TestStrategyImage(unittest.TestCase):
    def test_normalize_empty(self):
        self.assertEqual(normalize_strategy_image_url(""), "")
        self.assertEqual(normalize_strategy_image_url(None), "")
        self.assertEqual(normalize_strategy_image_url("__GENERATING__"), "")

    def test_encode_decode(self):
        url = "https://cdn.example.com/a.jpg"
        raw = encode_strategy_image_content(url)
        self.assertTrue(raw.startswith(STRATEGY_IMAGE_PREFIX))
        self.assertTrue(is_strategy_image_content(raw))
        self.assertEqual(decode_strategy_image_url(raw), url)
        self.assertFalse(is_strategy_image_content("hello"))

    def test_annotate_image(self):
        msg = {"role": "assistant", "content": encode_strategy_image_content("https://x/y.png")}
        out = annotate_chat_message(msg)
        self.assertEqual(out["content_type"], "image")
        self.assertEqual(out["image_url"], "https://x/y.png")
        self.assertEqual(out["content"], "")

    def test_annotate_text(self):
        msg = {"role": "assistant", "content": "hi"}
        out = annotate_chat_message(msg)
        self.assertEqual(out["content_type"], "text")
        self.assertEqual(out["image_url"], "")
        self.assertEqual(out["content"], "hi")

    def test_ws_payload_image_only(self):
        payload = assistant_message_payload("", "https://x/y.png")
        self.assertEqual(payload["type"], "message")
        self.assertEqual(payload["content_type"], "image")
        self.assertEqual(payload["image_url"], "https://x/y.png")
        self.assertEqual(payload["text"], "")


if __name__ == "__main__":
    unittest.main()
