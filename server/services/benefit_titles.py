"""福利商品多国家名称：绑定、校验、按 Accept-Language 选取。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# 前端 Accept-Language / ?lang 短码 → 商店国家码（模板 regions、titles 键）
# 大小写不敏感。不要把语种码直接当国家码（ja 不是 JA）。
_LANG_TO_REGION = {
    # 前端当前语种
    "en": "US",  # English
    "ja": "JP",  # 日本語 / 日本
    "ko": "KR",  # 한국어 / 韩国（商店码是 KR，不是 KO）
    "pt": "BR",  # Português / 巴西
    "es": "ES",  # Español / 西班牙
    "id": "ID",  # Bahasa Indonesia
    "ar": "SA",  # العربية / 默认沙特
    "ru": "RU",  # Русский / 俄罗斯
    "bg": "BG",  # Български / 保加利亚
    "tr": "TR",  # Türkçe / 土耳其
    "ir": "IR",  # 前端波斯语短码
    "fa": "IR",  # ISO 639-1 波斯语
    # 其它已支持语种
    "de": "DE",
    "fr": "FR",
    "it": "IT",
    "ms": "MY",
    "nl": "NL",
    "pl": "PL",
    "sv": "SE",
    "th": "TH",
    "tl": "PH",
    "vi": "VN",
    "zh": "CN",
}

_MAX_TITLE_LEN = 100


def normalize_titles_payload(raw: Any) -> dict[str, str]:
    """校验并规范化 titles 映射。空值/空串的国家会被丢掉。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("titles 必须是对象，例如 {\"US\":\"Weekly VIP\",\"JP\":\"週間VIP\"}")
    out: dict[str, str] = {}
    for key, val in raw.items():
        region = str(key or "").strip().upper()
        if region == "ALL":
            pass
        elif len(region) != 2 or not region.isalpha():
            raise ValueError(f"titles 的键必须是 2 位国家码或 ALL，收到: {key}")
        if val is None:
            continue
        title = str(val).strip()
        if not title:
            continue
        if len(title) > _MAX_TITLE_LEN:
            raise ValueError(f"titles[{region}] 不能超过 {_MAX_TITLE_LEN} 个字符")
        out[region] = title
    return out


def language_to_region(accept_language: Optional[str]) -> str:
    """Accept-Language / ?lang → 商店国家码。

    前端短码：en→US，ja→JP，ko→KR，pt→BR，es→ES，id→ID，
    ar→SA，ru→RU，bg→BG，tr→TR，ir/fa→IR。
    带地区时用地区：en-US→US，ja-JP→JP。无法识别返回空串。
    """
    raw = str(accept_language or "").strip()
    if not raw:
        return ""
    first = raw.split(",")[0].strip().replace("_", "-")
    first = first.split(";")[0].strip()
    parts = [p for p in first.split("-") if p]
    if not parts:
        return ""
    scripts = {"HANS", "HANT", "LATN", "CYRL", "ARAB"}
    for part in reversed(parts[1:]):
        region = part.upper()
        if len(region) == 2 and region.isalpha() and region not in scripts:
            return region
    lang = parts[0].lower()
    return _LANG_TO_REGION.get(lang) or ""


def title_lookup_keys(accept_language: Optional[str]) -> list[str]:
    """从 Accept-Language / ?lang 生成 titles 查找键，先国家后语种。"""
    raw = str(accept_language or "").strip()
    if not raw:
        return ["ALL"]
    first = raw.split(",")[0].strip().replace("_", "-")
    parts = [p for p in first.split("-") if p]
    keys: list[str] = []
    lang = (parts[0].lower() if parts else "")
    if len(parts) >= 2:
        region = parts[1].upper()
        if len(region) == 2 and region.isalpha():
            keys.append(region)
    if lang:
        mapped = _LANG_TO_REGION.get(lang)
        if mapped:
            keys.append(mapped)
        if len(lang) == 2 and lang.isalpha():
            keys.append(lang.upper())
            keys.append(lang)
    keys.append("ALL")
    seen: set[str] = set()
    ordered: list[str] = []
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        ordered.append(k)
    return ordered


def pick_title(titles_map: Optional[dict[str, str]], accept_language: Optional[str]) -> Optional[str]:
    """按 Accept-Language 从名称表取一条；没有命中返回 None。"""
    if not titles_map:
        return None
    index = {str(k).strip().upper(): v for k, v in titles_map.items() if v}
    # 小写语种键（en/ja）也允许
    for k, v in titles_map.items():
        if v and str(k).strip().islower():
            index.setdefault(str(k).strip(), v)
    for key in title_lookup_keys(accept_language):
        hit = index.get(key.upper()) or index.get(key)
        if hit:
            return str(hit)
    return None


def load_titles_maps(db, product_ids: Iterable[int]) -> dict[int, dict[str, str]]:
    from core.database import BenefitProductTitleORM

    ids = [int(i) for i in product_ids if i is not None]
    if not ids:
        return {}
    rows = (
        db.query(BenefitProductTitleORM)
        .filter(BenefitProductTitleORM.product_id.in_(ids))
        .all()
    )
    out: dict[int, dict[str, str]] = {}
    for row in rows:
        out.setdefault(row.product_id, {})[row.region_code] = row.title
    return out


def replace_product_titles(db, product_id: int, titles_map: dict[str, str]) -> None:
    """整表替换该商品的名称绑定。传空对象即清空。"""
    from core.database import BenefitProductTitleORM

    db.query(BenefitProductTitleORM).filter(
        BenefitProductTitleORM.product_id == product_id
    ).delete(synchronize_session=False)
    now = datetime.now(timezone.utc)
    for region, title in titles_map.items():
        db.add(
            BenefitProductTitleORM(
                product_id=product_id,
                region_code=region,
                title=title,
                created_at=now,
                updated_at=now,
            )
        )
