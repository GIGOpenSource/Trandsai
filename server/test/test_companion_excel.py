"""Unit tests for companion Excel import/export (no DB)."""
import unittest

from services.companion_excel import (
    EXPORT_COLUMNS,
    TEMPLATE_SAMPLE_ROWS,
    apply_companions_import,
    build_companions_template_xlsx,
    build_companions_xlsx,
    normalize_import_row,
    orm_row_to_export_dict,
    parse_companions_xlsx,
)


class _FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestCompanionExcel(unittest.TestCase):
    def test_columns_count(self):
        self.assertEqual(len(EXPORT_COLUMNS), 32)
        self.assertEqual(EXPORT_COLUMNS[0], "id")
        self.assertEqual(EXPORT_COLUMNS[-1], "tags")

    def test_template_has_two_samples(self):
        self.assertEqual(len(TEMPLATE_SAMPLE_ROWS), 2)
        raw = build_companions_template_xlsx()
        parsed, errs = parse_companions_xlsx(raw)
        self.assertEqual(errs, [])
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "示例小雅")
        self.assertEqual(parsed[1]["name"], "示例阿凯")
        self.assertEqual(str(parsed[0].get("id") or "").strip(), "")

    def test_roundtrip_headers_and_row(self):
        row = orm_row_to_export_dict(
            _FakeRow(
                id="abcd1234",
                name="测试",
                age=22,
                gender="女",
                city="上海",
                personality="温柔体贴善于倾听",
                background="经历丰富内心细腻的人设背景",
                speech_style="说话自然亲切有温度一些",
                hobbies="读书",
                values="真诚",
                fears="孤独",
                love_view="认真",
                daily_routine="早起",
                favorite_things="咖啡",
                mbti="INFP",
                sexual_orientation="异性恋",
                life_story="故事",
                cultural_values="文化",
                gender_perspective="视角",
                avatar_url="https://example.com/a.png",
                created_by="admin",
                language="zh",
                persona_axes='{"a":1}',
                country="CN",
                region_key="cn-east",
                tier="featured",
                created_at=None,
                is_deleted=False,
                appearance="长发",
                description="简介",
                region_label="华东",
                tags=["warm", "gender:女"],
            )
        )
        raw = build_companions_xlsx([row])
        parsed, errs = parse_companions_xlsx(raw)
        self.assertEqual(errs, [])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], "abcd1234")
        self.assertEqual(parsed[0]["name"], "测试")
        self.assertIn("warm", str(parsed[0]["tags"]))

    def test_normalize_skips_empty_and_created_at(self):
        raw = {
            "_row": 2,
            "id": "abcd1234",
            "name": "Alice",
            "age": 20,
            "gender": "女",
            "city": "",
            "created_at": "2020-01-01T00:00:00+00:00",
            "tags": '["ops"]',
            "is_deleted": "FALSE",
        }
        for key in EXPORT_COLUMNS:
            raw.setdefault(key, "")
        out = normalize_import_row(raw)
        self.assertNotIn("city", out)
        self.assertNotIn("created_at", out)
        self.assertEqual(out["name"], "Alice")
        self.assertEqual(out["age"], 20)
        self.assertEqual(out["tags"], ["ops"])
        self.assertIs(out["is_deleted"], False)

    def test_import_upsert_callbacks(self):
        store = {}

        def exists(cid):
            return cid in store

        def create(payload):
            cid = payload.get("id") or "new00001"
            store[cid] = dict(payload)
            store[cid]["id"] = cid
            return cid

        def update(cid, payload):
            store[cid].update(payload)

        def set_deleted(cid, flag):
            store[cid]["is_deleted"] = flag

        # build two excel-like rows
        def full(**kwargs):
            base = {k: "" for k in EXPORT_COLUMNS}
            base.update(kwargs)
            return base

        rows = [
            full(_row=2, name="A", age=18, gender="女", personality="温柔体贴善于倾听",
                 background="经历丰富内心细腻背景文", speech_style="说话自然亲切有温度呀"),
            full(_row=3, id="exist001", name="B", city="北京"),
        ]
        store["exist001"] = {"id": "exist001", "name": "Old", "city": "上海"}

        result = apply_companions_import(
            rows,
            create_fn=create,
            update_fn=update,
            exists_fn=exists,
            set_deleted_fn=set_deleted,
        )
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(store["exist001"]["city"], "北京")
        self.assertEqual(store["exist001"]["name"], "B")

    def test_missing_optional_columns_still_parse(self):
        from openpyxl import Workbook
        from io import BytesIO

        wb = Workbook()
        ws = wb.active
        ws.title = "companions"
        ws.append(["id", "name", "age", "gender", "city"])
        ws.append(["", "小新", 20, "female", "上海"])
        buf = BytesIO()
        wb.save(buf)
        parsed, errs = parse_companions_xlsx(buf.getvalue())
        self.assertEqual(errs, [])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "小新")

    def test_unrecognized_header_has_reason(self):
        from openpyxl import Workbook
        from io import BytesIO

        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "城市"])
        ws.append(["小新", "上海"])
        buf = BytesIO()
        wb.save(buf)
        parsed, errs = parse_companions_xlsx(buf.getvalue())
        self.assertEqual(parsed, [])
        self.assertTrue(errs)
        self.assertIn("表头", errs[0]["reason"])

    def test_gender_english_normalizes(self):
        raw = {k: "" for k in EXPORT_COLUMNS}
        raw.update({"_row": 2, "name": "A", "gender": "female"})
        out = normalize_import_row(raw)
        self.assertEqual(out["gender"], "女")

    def test_invalid_tags_reason(self):
        raw = {k: "" for k in EXPORT_COLUMNS}
        raw.update({"_row": 2, "name": "A", "tags": "warm,cheerful"})
        with self.assertRaises(Exception) as ctx:
            normalize_import_row(raw)
        self.assertIn("JSON", str(ctx.exception))

    def test_import_row_error_is_chinese(self):
        store = {}

        def exists(cid):
            return False

        def create(payload):
            raise ValueError("should not create")

        def update(cid, payload):
            raise ValueError("should not update")

        def set_deleted(cid, flag):
            pass

        rows = [{k: "" for k in EXPORT_COLUMNS}]
        rows[0]["_row"] = 2
        rows[0]["age"] = 18
        result = apply_companions_import(
            rows,
            create_fn=create,
            update_fn=update,
            exists_fn=exists,
            set_deleted_fn=set_deleted,
        )
        self.assertEqual(result["failed"], 1)
        self.assertIn("姓名", result["errors"][0]["reason"])
        from services.companion_excel import summarize_import_result
        msg = summarize_import_result(result)
        self.assertIn("失败", msg)
        self.assertIn("姓名", msg)

    def test_name_too_long_reason(self):
        raw = {k: "" for k in EXPORT_COLUMNS}
        raw.update({"_row": 2, "name": "这是一个超过二十个字的超长姓名ABCDEF"})
        with self.assertRaises(Exception) as ctx:
            normalize_import_row(raw)
        self.assertIn("20", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
