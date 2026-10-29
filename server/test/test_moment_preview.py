"""朋友圈 preview_url：用户端回退原图，后台原样返回。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.moment_urls import moment_public_image_fields, normalize_preview_pos


class TestMomentPreviewUrl(unittest.TestCase):
    def test_fallback_to_original(self):
        m = SimpleNamespace(
            image_url="https://cdn.example.com/images/a.jpg",
            preview_url=None,
            preview_pos=None,
        )
        fields = moment_public_image_fields(m)
        self.assertEqual(fields["image_url"], "https://cdn.example.com/images/a.jpg")
        self.assertEqual(fields["preview_url"], "https://cdn.example.com/images/a.jpg")
        self.assertIsNone(fields["preview_pos"])
        self.assertFalse(fields["image_generating"])

    def test_uses_preview_when_set(self):
        m = SimpleNamespace(
            image_url="https://cdn.example.com/images/full.jpg",
            preview_url="https://cdn.example.com/moments/crop.jpg",
            preview_pos={"x": 0.5, "y": 0.32},
        )
        fields = moment_public_image_fields(m)
        self.assertEqual(fields["image_url"], "https://cdn.example.com/images/full.jpg")
        self.assertEqual(fields["preview_url"], "https://cdn.example.com/moments/crop.jpg")
        self.assertEqual(fields["preview_pos"], {"x": 0.5, "y": 0.32})


class TestPreviewPos(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(
            normalize_preview_pos({"x": 0.5, "y": 0.32}),
            {"x": 0.5, "y": 0.32},
        )

    def test_null_clears(self):
        self.assertIsNone(normalize_preview_pos(None))

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            normalize_preview_pos({"x": 0.5, "y": 1.2})
        with self.assertRaises(ValueError):
            normalize_preview_pos({"x": -0.01, "y": 0})

    def test_missing_axis(self):
        with self.assertRaises(ValueError):
            normalize_preview_pos({"y": 0.3})


class TestMomentPreviewUrl(unittest.TestCase):
    def test_fallback_to_original(self):
        m = SimpleNamespace(
            image_url="https://cdn.example.com/images/a.jpg",
            preview_url=None,
        )
        fields = moment_public_image_fields(m)
        self.assertEqual(fields["image_url"], "https://cdn.example.com/images/a.jpg")
        self.assertEqual(fields["preview_url"], "https://cdn.example.com/images/a.jpg")
        self.assertFalse(fields["image_generating"])

    def test_uses_preview_when_set(self):
        m = SimpleNamespace(
            image_url="https://cdn.example.com/images/full.jpg",
            preview_url="https://cdn.example.com/moments/crop.jpg",
        )
        fields = moment_public_image_fields(m)
        self.assertEqual(fields["image_url"], "https://cdn.example.com/images/full.jpg")
        self.assertEqual(fields["preview_url"], "https://cdn.example.com/moments/crop.jpg")


if __name__ == "__main__":
    unittest.main()
