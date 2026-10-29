"""资料审核：匹配字段即时落库并规范为 male/female。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from services.profile_review import (  # noqa: E402
    IMMEDIATE_PROFILE_FIELDS,
    merge_user_view,
    reject_profile_review,
    submit_profile_review,
)


class TestProfileReviewImmediateFields(unittest.TestCase):
    def test_merge_user_view_still_overlays_review_fields(self):
        user = MagicMock()
        user.nickname = "old"
        user.gender = "male"
        user.sexual_orientation = ""
        user.preferred_gender = ""
        user.chat_purpose = ""
        user.avatar_url = ""
        user.age = 20
        user.region = ""
        user.occupation = ""

        pending = MagicMock()
        pending.status = "pending"
        pending.pending_data = {"nickname": "new", "age": 21, "gender": "女", "region": "东京"}

        view = merge_user_view(user, pending)
        self.assertEqual(view["nickname"], "new")
        self.assertEqual(view["region"], "东京")
        self.assertEqual(view["age"], 20)
        self.assertEqual(view["gender"], "male")
        self.assertIn("nickname", view.get("profile_pending") or {})
        self.assertIn("region", view.get("profile_pending") or {})
        self.assertNotIn("age", view.get("profile_pending") or {})
        self.assertNotIn("gender", view.get("profile_pending") or {})

    @patch("services.profile_review.get_db")
    def test_submit_applies_gender_immediately_as_canonical(self, mock_get_db):
        user = MagicMock()
        user.id = 63
        user.username = "test@example.com"
        user.nickname = "liang"
        user.gender = "male"
        user.sexual_orientation = "heterosexual"
        user.preferred_gender = "female"
        user.chat_purpose = "companionship"
        user.avatar_url = ""
        user.age = 20
        user.region = ""
        user.occupation = ""

        db = MagicMock()
        filt = MagicMock()
        filt.first.return_value = user
        filt.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value = filt
        mock_get_db.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = submit_profile_review(63, {"gender": "女"})

        self.assertEqual(user.gender, "female")
        self.assertFalse(result.get("unchanged"))
        self.assertEqual(result["gender"], "female")
        db.add.assert_not_called()

    @patch("services.profile_review.get_db")
    def test_submit_age_and_orientation_immediately(self, mock_get_db):
        user = MagicMock()
        user.id = 63
        user.username = "test@example.com"
        user.nickname = "liang"
        user.gender = "male"
        user.sexual_orientation = "heterosexual"
        user.preferred_gender = "female"
        user.chat_purpose = "companionship"
        user.avatar_url = ""
        user.age = 20
        user.region = "大阪"
        user.occupation = "职员"

        db = MagicMock()
        filt = MagicMock()
        filt.first.return_value = user
        filt.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value = filt
        mock_get_db.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = submit_profile_review(
            63,
            {
                "nickname": "liangwater",
                "sexual_orientation": "homosexual",
                "gender": "男",
                "age": 22,
                "region": "东京",
                "occupation": "学生",
            },
        )

        self.assertEqual(user.gender, "male")
        self.assertEqual(user.age, 22)
        self.assertEqual(user.sexual_orientation, "homosexual")
        self.assertEqual(user.nickname, "liang")
        self.assertEqual(user.region, "大阪")
        self.assertEqual(user.occupation, "职员")
        db.add.assert_called_once()
        pending_row = db.add.call_args[0][0]
        self.assertEqual(
            pending_row.pending_data,
            {"nickname": "liangwater", "region": "东京", "occupation": "学生"},
        )
        self.assertEqual(result["profile_review_status"], "pending")
        self.assertEqual(result["age"], 22)
        self.assertEqual(result["sexual_orientation"], "homosexual")
        self.assertEqual(result["nickname"], "liangwater")
        self.assertNotIn("age", result.get("profile_pending") or {})
        self.assertNotIn("gender", result.get("profile_pending") or {})
        self.assertNotIn("sexual_orientation", result.get("profile_pending") or {})

    @patch("services.profile_review.get_db")
    def test_submit_avatar_still_goes_to_review(self, mock_get_db):
        user = MagicMock()
        user.id = 63
        user.username = "test@example.com"
        user.nickname = "liang"
        user.gender = "male"
        user.sexual_orientation = "homosexual"
        user.preferred_gender = ""
        user.chat_purpose = ""
        user.avatar_url = ""
        user.age = 22
        user.region = ""
        user.occupation = ""

        db = MagicMock()
        filt = MagicMock()
        filt.first.return_value = user
        filt.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value = filt
        mock_get_db.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = submit_profile_review(
            63,
            {"avatar_url": "https://storage.googleapis.com/trandsbucket/images/x.png"},
        )

        self.assertEqual(user.avatar_url, "")
        db.add.assert_called_once()
        self.assertEqual(result["profile_review_status"], "pending")
        self.assertIn("avatar_url", result.get("profile_pending") or {})

    @patch("services.profile_review.get_db")
    def test_submit_backgrounds_goes_to_review(self, mock_get_db):
        user = MagicMock()
        user.id = 63
        user.username = "test@example.com"
        user.nickname = "liang"
        user.gender = "male"
        user.sexual_orientation = "homosexual"
        user.preferred_gender = ""
        user.chat_purpose = ""
        user.avatar_url = ""
        user.profile_backgrounds = []
        user.age = 22
        user.region = ""
        user.occupation = ""

        db = MagicMock()
        filt = MagicMock()
        filt.first.return_value = user
        filt.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value = filt
        mock_get_db.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        url = "https://storage.googleapis.com/trandsbucket/images/94f66bab.jpg"
        result = submit_profile_review(63, {"profile_backgrounds": [url]})

        self.assertEqual(user.profile_backgrounds, [])
        db.add.assert_called_once()
        self.assertEqual(result["profile_review_status"], "pending")
        self.assertEqual(result["profile_background_review_status"], "pending")
        self.assertEqual(result["profile_backgrounds"], [url])
        self.assertEqual(result["profile_backgrounds_pending"], [url])
        self.assertIn("profile_backgrounds", result.get("profile_pending") or {})

    @patch("services.profile_review.get_db")
    def test_clear_backgrounds_applies_immediately(self, mock_get_db):
        user = MagicMock()
        user.id = 63
        user.username = "test@example.com"
        user.nickname = "liang"
        user.gender = "male"
        user.sexual_orientation = ""
        user.preferred_gender = ""
        user.chat_purpose = ""
        user.avatar_url = ""
        user.profile_backgrounds = ["https://storage.googleapis.com/trandsbucket/images/old.jpg"]
        user.age = 22
        user.region = ""
        user.occupation = ""

        db = MagicMock()
        filt = MagicMock()
        filt.first.return_value = user
        filt.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value = filt
        mock_get_db.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = submit_profile_review(63, {"profile_backgrounds": []})
        self.assertEqual(user.profile_backgrounds, [])
        db.add.assert_not_called()
        self.assertIsNone(result.get("profile_background_review_status"))
        self.assertEqual(result.get("profile_backgrounds"), [])

    def test_immediate_fields_are_expected_set(self):
        self.assertIn("gender", IMMEDIATE_PROFILE_FIELDS)
        self.assertIn("age", IMMEDIATE_PROFILE_FIELDS)
        self.assertIn("sexual_orientation", IMMEDIATE_PROFILE_FIELDS)
        self.assertNotIn("nickname", IMMEDIATE_PROFILE_FIELDS)
        self.assertNotIn("region", IMMEDIATE_PROFILE_FIELDS)
        self.assertNotIn("occupation", IMMEDIATE_PROFILE_FIELDS)
        self.assertNotIn("avatar_url", IMMEDIATE_PROFILE_FIELDS)

    @patch("services.profile_review.get_db")
    def test_reject_profile_review_clears_other_pending_for_same_user(self, mock_get_db):
        review = MagicMock()
        review.id = 10
        review.user_id = 63
        review.status = "pending"

        stale_pending = MagicMock()
        stale_pending.id = 7
        stale_pending.user_id = 63
        stale_pending.status = "pending"

        db = MagicMock()

        target_q = MagicMock()
        target_f = MagicMock()
        target_q.filter.return_value = target_f
        target_f.first.return_value = review

        other_q = MagicMock()
        other_f1 = MagicMock()
        other_f2 = MagicMock()
        other_q.filter.return_value = other_f1
        other_f1.filter.return_value = other_f2
        other_f2.all.return_value = [stale_pending]

        db.query.side_effect = [target_q, other_q]
        mock_get_db.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = reject_profile_review(10, reason="bad avatar", reviewer="admin")

        self.assertTrue(result["ok"])
        self.assertEqual(review.status, "rejected")
        self.assertEqual(stale_pending.status, "rejected")
        self.assertEqual(stale_pending.reviewed_by, "admin")


if __name__ == "__main__":
    unittest.main()
