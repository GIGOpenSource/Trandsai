"""Our Moments 私密相册：次数字段、不再下发文案。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.private_album import _album_payload, photo_to_admin


class TestPrivateAlbumPayload(unittest.TestCase):
    def test_admin_has_unlock_x_no_copy(self):
        row = SimpleNamespace(
            id=12,
            companion_id="acb09f96",
            image_url="https://x/y.png",
            unlock_x=5,
            sort_order=0,
            is_enabled=True,
            created_at=None,
            updated_at=None,
        )
        d = photo_to_admin(row)
        self.assertEqual(d["unlock_x"], 5)
        self.assertNotIn("countdown_text", d)
        self.assertNotIn("success_text", d)

    def test_user_payload_counts_no_copy(self):
        photos = [
            SimpleNamespace(id=1, sort_order=0, image_url="https://a", unlock_x=5),
            SimpleNamespace(id=2, sort_order=10, image_url="https://b", unlock_x=5),
        ]
        progress = SimpleNamespace(
            unlocked_photo_ids=[1],
            current_photo_id=2,
            current_unlock_x=5,
            progress_count=3,
        )
        out = _album_payload("acb09f96", photos, progress)
        self.assertEqual(out["remaining_messages"], 2)
        self.assertEqual(out["unlock_x"], 5)
        self.assertEqual(out["unlocked_count"], 1)
        self.assertFalse(out["empty"])
        self.assertNotIn("countdown_text", out)
        self.assertNotIn("unlock_success_text", out)
        self.assertNotIn("empty_hint", out)
        self.assertEqual(out["photos"][0]["image_url"], "https://a")
        self.assertEqual(out["photos"][1]["image_url"], "https://b")
        self.assertFalse(out["photos"][1]["unlocked"])

    def test_last_unlock_keeps_unlocked_toast(self):
        photos = [
            SimpleNamespace(id=1, sort_order=0, image_url="https://a", unlock_x=10),
        ]
        progress = SimpleNamespace(
            unlocked_photo_ids=[1],
            current_photo_id=None,
            current_unlock_x=10,
            progress_count=0,
        )
        just = [{"photo_id": 1, "image_url": "https://a"}]
        out = _album_payload("acb09f96", photos, progress, just_unlocked=just)
        self.assertTrue(out["all_unlocked"])
        self.assertEqual(out["remaining_messages"], 0)
        self.assertEqual(out["just_unlocked"], just)
        self.assertEqual(out["photos"][0]["image_url"], "https://a")


if __name__ == "__main__":
    unittest.main()
