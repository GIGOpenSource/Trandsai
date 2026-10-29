"""Companion 资料卡 Excel 导入/导出（字段与 companions 表一致）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

# 与 information_schema.companions 列顺序一致
EXPORT_COLUMNS: Tuple[str, ...] = (
    "id",
    "name",
    "age",
    "gender",
    "city",
    "personality",
    "background",
    "speech_style",
    "hobbies",
    "values",
    "fears",
    "love_view",
    "daily_routine",
    "favorite_things",
    "mbti",
    "sexual_orientation",
    "life_story",
    "cultural_values",
    "gender_perspective",
    "avatar_url",
    "created_by",
    "language",
    "persona_axes",
    "country",
    "region_key",
    "tier",
    "created_at",
    "is_deleted",
    "appearance",
    "description",
    "region_label",
    "tags",
)

# DB varchar 上限（text 列不截断到固定长，由业务 clamp 处理）
_VARCHAR_MAX = {
    "id": 8,
    "name": 20,
    "gender": 2,
    "city": 20,
    "mbti": 10,
    "sexual_orientation": 20,
    "created_by": 64,
    "language": 10,
    "country": 8,
    "region_key": 32,
    "tier": 20,
    "region_label": 64,
}

# 导入时忽略（系统字段）；更新时空单元格不覆盖
_IMPORT_SKIP_KEYS = frozenset({"created_at"})
_IMPORT_MAX_ROWS = 1000
SHEET_NAME = "companions"
_IDENTITY_HEADERS = frozenset({"id", "name"})
_HEADER_ALIASES = {c.lower(): c for c in EXPORT_COLUMNS}

_FIELD_LABELS = {
    "id": "id",
    "name": "姓名",
    "age": "年龄",
    "gender": "性别",
    "city": "城市",
    "personality": "性格",
    "background": "背景",
    "speech_style": "说话风格",
    "hobbies": "爱好",
    "values": "价值观",
    "fears": "恐惧",
    "love_view": "恋爱观",
    "daily_routine": "作息",
    "favorite_things": "喜欢的事物",
    "mbti": "MBTI",
    "sexual_orientation": "性取向",
    "life_story": "人生故事",
    "cultural_values": "文化价值观",
    "gender_perspective": "性别观念",
    "avatar_url": "头像URL",
    "created_by": "创建人",
    "language": "语言",
    "persona_axes": "人设轴",
    "country": "国家",
    "region_key": "地区标识",
    "tier": "分层",
    "created_at": "创建时间",
    "is_deleted": "是否删除",
    "appearance": "外形",
    "description": "简介",
    "region_label": "地区名称",
    "tags": "标签",
    "header": "表头",
    "file": "文件",
}


class CompanionImportError(ValueError):
    """带字段名的导入错误，reason 给人看。"""

    def __init__(self, reason: str, field: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.field = field or ""


def _field_label(key: str) -> str:
    return _FIELD_LABELS.get(key, key or "字段")


def _header_error(reason: str, field: str = "header") -> Dict[str, Any]:
    return {"row": 1, "id": "", "name": "", "field": field, "reason": reason}


def format_import_error(exc: BaseException) -> Dict[str, str]:
    """把异常转成前端可展示的 {field, reason}。"""
    if isinstance(exc, CompanionImportError):
        return {"field": exc.field, "reason": exc.reason}

    try:
        from pydantic import ValidationError
    except Exception:
        ValidationError = tuple()  # type: ignore

    if ValidationError and isinstance(exc, ValidationError):
        parts: List[str] = []
        first_field = ""
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()) if x is not None)
            if not first_field:
                first_field = loc
            label = _field_label(loc)
            etype = err.get("type") or ""
            if etype in ("missing", "value_error.missing"):
                parts.append(f"{label} 不能为空")
            elif etype in ("string_too_short", "value_error.any_str.min_length"):
                parts.append(f"{label} 长度不足")
            elif etype in ("string_too_long", "value_error.any_str.max_length"):
                parts.append(f"{label} 超出最大长度")
            elif etype in ("string_pattern_mismatch", "value_error.str.regex") and loc == "gender":
                parts.append("性别只能是 男 / 女 / 保密（也可用 male/female）")
            elif etype in ("int_parsing", "type_error.integer"):
                parts.append(f"{label} 必须是整数")
            elif etype in ("less_than_equal", "greater_than_equal"):
                parts.append(f"{label} 超出允许范围")
            else:
                msg = err.get("msg") or "格式不正确"
                if msg.lower().startswith("value error, "):
                    msg = msg[13:]
                parts.append(f"{label}: {msg}")
        return {
            "field": first_field,
            "reason": "；".join(parts) if parts else "资料校验失败",
        }

    orig = getattr(exc, "orig", None)
    raw = str(orig or exc)
    low = raw.lower()
    if "too long" in low or "varying(" in low:
        return {
            "field": "",
            "reason": (
                "字段超出数据库长度限制"
                "（id 最多 8 字，姓名最多 20 字，城市最多 20 字，性别为 男/女/保密）"
                f"：{raw}"
            ),
        }
    if "unique" in low or "duplicate" in low:
        return {"field": "id", "reason": f"id 或「姓名+城市」与已有记录冲突：{raw}"}
    if "not null" in low or "null value" in low:
        return {"field": "", "reason": f"必填字段为空，无法写入：{raw}"}

    text = raw.strip() or exc.__class__.__name__
    if len(text) > 400:
        text = text[:400] + "…"
    return {"field": "", "reason": text}


def summarize_import_result(result: Dict[str, Any]) -> str:
    """生成导入结果提示（成功/部分失败都走这段，方便前端直接 toast message）。"""
    failed = int(result.get("failed") or 0)
    created = int(result.get("created") or 0)
    updated = int(result.get("updated") or 0)
    skipped = int(result.get("skipped") or 0)
    total = int(result.get("total") or 0)
    errors = result.get("errors") or []
    if failed:
        bits = [
            f"导入完成但有 {failed} 行失败（共 {total} 行：新建 {created}，更新 {updated}，跳过 {skipped}）"
        ]
        for err in errors[:5]:
            row = err.get("row")
            reason = (err.get("reason") or "未知错误").strip()
            who = (err.get("name") or err.get("id") or "").strip()
            prefix = f"第{row}行" if row else "错误"
            if who:
                prefix = f"{prefix}「{who}」"
            bits.append(f"{prefix}：{reason}")
        extra = failed - min(len(errors), 5)
        if extra > 0:
            bits.append(f"另有 {extra} 条错误见 results.errors")
        return "。".join(bits)
    return f"导入成功：新建 {created}，更新 {updated}，跳过 {skipped}"

# 导入模板示例（id 留空表示新建；导入前请删改示例行）
TEMPLATE_SAMPLE_ROWS: Tuple[Dict[str, Any], ...] = (
    {
        "id": "",
        "name": "示例小雅",
        "age": 23,
        "gender": "女",
        "city": "上海",
        "personality": "温柔体贴善于倾听，说话轻声细语",
        "background": "在上海长大的职场新人，喜欢周末逛书店和咖啡馆",
        "speech_style": "说话自然亲切有温度，偶尔撒娇",
        "hobbies": "阅读、手冲咖啡、城市漫步",
        "values": "真诚、尊重边界、慢慢来",
        "fears": "被忽视、关系突然变淡",
        "love_view": "希望稳定而有回应的陪伴",
        "daily_routine": "早起通勤，晚间散步或看剧，周末睡到自然醒",
        "favorite_things": "拿铁、纸质书、晚风",
        "mbti": "INFP",
        "sexual_orientation": "异性恋",
        "life_story": "童年随父母在江南小城生活，大学后来到上海工作，逐渐习惯快节奏也保留慢生活习惯。",
        "cultural_values": "重视家庭与朋友，相信细水长流",
        "gender_perspective": "希望关系里彼此平等、互相照顾",
        "avatar_url": "",
        "created_by": "admin",
        "language": "zh",
        "persona_axes": "",
        "country": "CN",
        "region_key": "cn-east",
        "tier": "featured",
        "created_at": "",
        "is_deleted": False,
        "appearance": "黑色长发，气质清冷温柔",
        "description": "温柔的上海女孩，适合日常陪伴聊天",
        "region_label": "华东",
        "tags": '["warm"]',
    },
    {
        "id": "",
        "name": "示例阿凯",
        "age": 25,
        "gender": "男",
        "city": "Tokyo",
        "personality": "开朗幽默，喜欢分享日常见闻",
        "background": "在东京工作的设计师，热爱街头摄影和周末露营",
        "speech_style": "轻松随意，带一点吐槽和鼓励",
        "hobbies": "摄影、露营、打游戏",
        "values": "自由、诚实、把事情做好",
        "fears": "无聊的重复生活",
        "love_view": "想找能一起出门玩也能安静待着的人",
        "daily_routine": "白天上班，晚上修图或和朋友喝酒，周末出城",
        "favorite_things": "胶片相机、拉面、深夜电台",
        "mbti": "ENFP",
        "sexual_orientation": "异性恋",
        "life_story": "高中开始接触设计，毕业后搬到东京，一边工作一边用镜头记录城市角落。",
        "cultural_values": "尊重个性，也在意团队默契",
        "gender_perspective": "关系里更看重沟通和共同成长",
        "avatar_url": "",
        "created_by": "admin",
        "language": "en",
        "persona_axes": "",
        "country": "JP",
        "region_key": "jp-kanto",
        "tier": "candidate",
        "created_at": "",
        "is_deleted": False,
        "appearance": "短发，笑起来有虎牙",
        "description": "东京设计师，聊天轻松有趣",
        "region_label": "Kanto",
        "tags": '["cheerful"]',
    },
)


def build_companions_template_xlsx() -> bytes:
    """下载用导入模板：表头 + 两条示例数据。"""
    return build_companions_xlsx(TEMPLATE_SAMPLE_ROWS)


def orm_row_to_export_dict(row: Any) -> Dict[str, Any]:
    """CompanionORM → 导出行 dict。"""
    tags = getattr(row, "tags", None)
    if tags is None:
        tags_val = "[]"
    elif isinstance(tags, str):
        tags_val = tags
    else:
        tags_val = json.dumps(list(tags), ensure_ascii=False)

    created_at = getattr(row, "created_at", None)
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_val = created_at.isoformat()
    else:
        created_at_val = created_at or ""

    is_deleted = getattr(row, "is_deleted", False)
    return {
        "id": getattr(row, "id", "") or "",
        "name": getattr(row, "name", "") or "",
        "age": getattr(row, "age", None),
        "gender": _export_gender(getattr(row, "gender", None)),
        "city": getattr(row, "city", "") or "",
        "personality": getattr(row, "personality", "") or "",
        "background": getattr(row, "background", "") or "",
        "speech_style": getattr(row, "speech_style", "") or "",
        "hobbies": getattr(row, "hobbies", "") or "",
        "values": getattr(row, "values", "") or "",
        "fears": getattr(row, "fears", "") or "",
        "love_view": getattr(row, "love_view", "") or "",
        "daily_routine": getattr(row, "daily_routine", "") or "",
        "favorite_things": getattr(row, "favorite_things", "") or "",
        "mbti": getattr(row, "mbti", "") or "",
        "sexual_orientation": getattr(row, "sexual_orientation", "") or "",
        "life_story": getattr(row, "life_story", "") or "",
        "cultural_values": getattr(row, "cultural_values", "") or "",
        "gender_perspective": getattr(row, "gender_perspective", "") or "",
        "avatar_url": getattr(row, "avatar_url", "") or "",
        "created_by": getattr(row, "created_by", "") or "",
        "language": getattr(row, "language", "") or "",
        "persona_axes": getattr(row, "persona_axes", "") or "",
        "country": getattr(row, "country", "") or "",
        "region_key": getattr(row, "region_key", "") or "",
        "tier": getattr(row, "tier", "") or "",
        "created_at": created_at_val,
        "is_deleted": bool(is_deleted) if is_deleted is not None else False,
        "appearance": getattr(row, "appearance", "") or "",
        "description": getattr(row, "description", "") or "",
        "region_label": getattr(row, "region_label", "") or "",
        "tags": tags_val,
    }


def build_companions_xlsx(rows: Sequence[Dict[str, Any]]) -> bytes:
    """生成资料卡 Excel 字节。"""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for col, key in enumerate(EXPORT_COLUMNS, 1):
        cell = ws.cell(row=1, column=col, value=key)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin

    for r_idx, row in enumerate(rows, 2):
        for c_idx, key in enumerate(EXPORT_COLUMNS, 1):
            val = row.get(key, "")
            if key == "is_deleted":
                val = "TRUE" if val in (True, "TRUE", "true", "1", 1) else "FALSE"
            elif key == "tags" and not isinstance(val, str):
                val = json.dumps(val if val is not None else [], ensure_ascii=False)
            elif val is None:
                val = ""
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.border = thin
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    for col in range(1, len(EXPORT_COLUMNS) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 16
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _export_gender(val: Any) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    try:
        from core.gender import stored_gender
        return stored_gender(s)
    except Exception:
        return s


def _cell_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val).strip()


def _parse_bool(val: Any) -> Optional[bool]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return val
    s = _cell_str(val).upper()
    if s in ("TRUE", "1", "YES", "Y", "是"):
        return True
    if s in ("FALSE", "0", "NO", "N", "否"):
        return False
    raise CompanionImportError(
        f"是否删除只能填 TRUE/FALSE（当前：{val!r}）",
        field="is_deleted",
    )


def _parse_age(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        raise CompanionImportError("年龄必须是数字", field="age")
    if isinstance(val, (int, float)):
        return int(val)
    s = _cell_str(val)
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError) as e:
        raise CompanionImportError(f"年龄必须是数字（当前：{s}）", field="age") from e


def _parse_tags(val: Any) -> Optional[list]:
    if val is None or val == "":
        return None
    if isinstance(val, list):
        return val
    s = _cell_str(val)
    if not s:
        return None
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise CompanionImportError(
            f'tags 必须是 JSON 数组，例如 ["warm"]（当前无法解析：{s[:80]}）',
            field="tags",
        ) from e
    if not isinstance(data, list):
        raise CompanionImportError(
            f'tags 必须是 JSON 数组，例如 ["warm"]（当前类型不对）',
            field="tags",
        )
    return data


def parse_companions_xlsx(content: bytes) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    解析 Excel。
    返回 (rows, header_errors)；header_errors 非空时 rows 为空。
    每行含 _row（Excel 行号，1-based）。
    多余列忽略；缺列按空值处理（不再因缺 tags 等整表失败）。
    """
    try:
        wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        return [], [_header_error(
            f"无法解析 Excel，请上传 .xlsx（与导出/模板相同）。详情：{e}",
            field="file",
        )]
    ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return [], [_header_error("Excel 是空的，请使用导出文件或下载模板后再导入。", field="file")]

    headers = [_cell_str(h) for h in (header or ())]
    if headers:
        headers[0] = headers[0].lstrip("\ufeff")

    header_index: Dict[str, int] = {}
    for i, h in enumerate(headers):
        if not h:
            continue
        key = _HEADER_ALIASES.get(h.lower())
        if key and key not in header_index:
            header_index[key] = i

    if not header_index:
        preview = "、".join(x for x in headers if x)[:120] or "(空)"
        return [], [_header_error(
            "未识别到表头。第一行必须是英文字段名（id、name、age、gender…），"
            f"请使用「导出」下载的文件或「下载模板」。当前第一行：{preview}",
        )]
    if not (header_index.keys() & _IDENTITY_HEADERS):
        found = "、".join(header_index.keys())
        return [], [_header_error(
            "表头缺少 id 和 name，无法判断新建还是更新。"
            f"请使用导出文件或模板。已识别列：{found or '无'}",
        )]

    parsed: List[Dict[str, Any]] = []
    for excel_row, values in enumerate(rows_iter, start=2):
        values = list(values or ())
        if all(v is None or _cell_str(v) == "" for v in values):
            continue
        item: Dict[str, Any] = {"_row": excel_row}
        for key in EXPORT_COLUMNS:
            idx = header_index.get(key)
            if idx is None:
                continue
            item[key] = values[idx] if idx < len(values) else None
        parsed.append(item)
        if len(parsed) > _IMPORT_MAX_ROWS:
            return [], [_header_error(
                f"超过导入上限 {_IMPORT_MAX_ROWS} 行，请拆分后分批导入。",
                field="file",
            )]

    if not parsed:
        return [], [_header_error("没有可导入的数据行（表头下一行起）。请检查是否只导入了空表。", field="file")]

    return parsed, []


def normalize_import_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Excel 原始行转为可写入 create/update 的 dict。
    空字符串字段不放入结果（更新时不覆盖）；新建时缺省由 clamp/默认值补齐。
    校验失败抛 ValueError。
    """
    out: Dict[str, Any] = {}
    excel_row = raw.get("_row")

    for key in EXPORT_COLUMNS:
        if key in _IMPORT_SKIP_KEYS:
            continue
        if key not in raw:
            continue
        cell = raw[key]

        if key == "age":
            age = _parse_age(cell)
            if age is not None:
                if age < 0 or age > 1000:
                    raise CompanionImportError("年龄必须在 0–1000 之间", field="age")
                out["age"] = age
            continue

        if key == "is_deleted":
            b = _parse_bool(cell)
            if b is not None:
                out["is_deleted"] = b
            continue

        if key == "tags":
            tags = _parse_tags(cell)
            if tags is not None:
                out["tags"] = tags
            continue

        s = _cell_str(cell)
        if s == "":
            continue

        if key == "gender":
            try:
                from core.gender import stored_gender
                s = stored_gender(s)
            except Exception:
                pass

        if key == "sexual_orientation":
            try:
                from services.recommend_tags import canonical_orientation
                canon = canonical_orientation(s)
                if canon:
                    s = canon
            except Exception:
                pass

        max_len = _VARCHAR_MAX.get(key)
        if max_len is not None and len(s) > max_len:
            raise CompanionImportError(
                f"{_field_label(key)}最多 {max_len} 个字，当前 {len(s)} 个字",
                field=key,
            )

        out[key] = s

    if "name" not in out and not _cell_str(raw.get("name")):
        # 更新时 name 可空（不改名）；新建时必须有 name —— 由调用方判断
        pass

    out["_row"] = excel_row
    return out


def apply_companions_import(
    rows: Sequence[Dict[str, Any]],
    *,
    create_fn,
    update_fn,
    exists_fn,
    set_deleted_fn,
) -> Dict[str, Any]:
    """
    批量导入：有 id 且存在 → 非空字段更新；否则新建。
    create_fn(payload) -> companion_id
    update_fn(companion_id, payload) -> None
    exists_fn(companion_id) -> bool
    set_deleted_fn(companion_id, is_deleted) -> None
    """
    created = updated = skipped = failed = 0
    errors: List[Dict[str, Any]] = []

    for raw in rows:
        row_no = raw.get("_row")
        try:
            payload = normalize_import_row(raw)
            cid = (payload.get("id") or "").strip()
            is_deleted = payload.pop("is_deleted", None)
            payload.pop("_row", None)

            if cid and exists_fn(cid):
                update_data = {k: v for k, v in payload.items() if k != "id"}
                if not update_data and is_deleted is None:
                    skipped += 1
                    continue
                if update_data:
                    update_fn(cid, update_data)
                if is_deleted is not None:
                    set_deleted_fn(cid, is_deleted)
                updated += 1
            else:
                if "name" not in payload:
                    raise CompanionImportError("新建必须填写姓名（name）", field="name")
                if not cid:
                    payload.pop("id", None)
                if not str(payload.get("city") or "").strip():
                    payload["city"] = "未知"
                if payload.get("age") is None:
                    payload["age"] = 18
                new_id = create_fn(payload)
                if is_deleted is not None:
                    set_deleted_fn(new_id, is_deleted)
                created += 1
        except Exception as e:
            failed += 1
            formatted = format_import_error(e)
            errors.append({
                "row": row_no,
                "id": _cell_str(raw.get("id")),
                "name": _cell_str(raw.get("name")),
                "field": formatted.get("field") or "",
                "reason": formatted.get("reason") or str(e),
            })
            logger.warning("companion import row %s failed: %s", row_no, e)

    return {
        "ok": failed == 0,
        "total": len(rows),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }
