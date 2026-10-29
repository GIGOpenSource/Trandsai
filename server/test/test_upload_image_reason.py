"""上传图预处理失败原因，供前台 message 展示。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.thumbnails import (
    normalize_upload_folder,
    parse_upload_quality,
    prepare_upload_image,
    sniff_image_mime,
)


class TestPrepareUploadReason(unittest.TestCase):
    def test_empty_file(self):
        prepared, reason = prepare_upload_image(b"")
        self.assertIsNone(prepared)
        self.assertEqual(reason, "image_empty")

    def test_garbage_bytes_decode_failed(self):
        prepared, reason = prepare_upload_image(b"not-an-image-at-all", "application/octet-stream")
        self.assertIsNone(prepared)
        self.assertEqual(reason, "image_decode_failed")

    def test_sniff_jpeg(self):
        jpeg_head = b"\xff\xd8\xff" + b"\x00" * 16
        self.assertEqual(sniff_image_mime(jpeg_head), "image/jpeg")


class TestUploadFolderAndQuality(unittest.TestCase):
    def test_folder_default(self):
        self.assertEqual(normalize_upload_folder(None), "images")
        self.assertEqual(normalize_upload_folder(""), "images")
        self.assertEqual(normalize_upload_folder("  /images/  "), "images")

    def test_folder_custom(self):
        self.assertEqual(normalize_upload_folder("moments"), "moments")
        self.assertEqual(normalize_upload_folder("moments/preview"), "moments/preview")

    def test_folder_rejects_traversal(self):
        with self.assertRaises(ValueError):
            normalize_upload_folder("../secret")
        with self.assertRaises(ValueError):
            normalize_upload_folder("a/../../b")

    def test_quality_parse(self):
        self.assertIsNone(parse_upload_quality(None))
        self.assertEqual(parse_upload_quality(50), 50)
        with self.assertRaises(ValueError):
            parse_upload_quality(0)
        with self.assertRaises(ValueError):
            parse_upload_quality(101)


if __name__ == "__main__":
    unittest.main()
