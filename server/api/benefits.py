"""
充值福利管理 API 路由

后台管理端点（需 admin 认证）：
- CRUD /api/admin/benefits/products        — 福利商品管理
- CRUD /api/admin/benefits/templates       — 福利模板管理
- CRUD /api/admin/benefits/templates/{id}/products   — 模板绑定商品
- CRUD /api/admin/benefits/templates/{id}/regions    — 模板绑定区域

前端查询端点（公开）：
- GET /api/benefits?type=subscribe|point   — 按区域返回福利（分组；不含限时商品）
- GET /api/benefits/limited-offer          — 当前模板中的限时优惠（登录未付费即开始倒计时）
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import case, func, or_

from api.admin import admin_auth_required
from core.app_key import (
    DEFAULT_APP_KEY,
    KNOWN_APP_KEYS,
    app_key_from_headers,
    app_key_meta,
    normalize_app_key,
    resolve_app_key_filter,
)
from core.i18n import _, benefit_product_display_name, get_ui_lang, normalize_ui_language
from core.database import (
    BenefitProductORM,
    BenefitProductTitleORM,
    BenefitTemplateORM,
    BenefitTemplateProductORM,
    BenefitTemplateRegionORM,
    ProductPriceORM,
    get_db,
)
from services.benefit_titles import (
    language_to_region,
    load_titles_maps,
    normalize_titles_payload,
    pick_title,
    replace_product_titles,
)

router = APIRouter(tags=["充值福利"])
logger = logging.getLogger(__name__)

LIMITED_DURATION_MAX = 3600
LIMITED_DURATION_DEFAULT = 1800


def _clamp_limited_duration(sec, *, enabled: bool) -> int:
    if not enabled:
        return 0
    try:
        n = int(sec or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = LIMITED_DURATION_DEFAULT
    return min(n, LIMITED_DURATION_MAX)


def _parse_limited_fields(data: dict, *, existing=None) -> tuple[bool, int]:
    enabled = bool(getattr(existing, "is_limited_offer", False)) if existing is not None else False
    duration = int(getattr(existing, "limited_duration_sec", 0) or 0) if existing is not None else 0
    if "is_limited_offer" in data:
        enabled = bool(data.get("is_limited_offer"))
    if "limited_duration_sec" in data:
        duration = data.get("limited_duration_sec")
    return enabled, _clamp_limited_duration(duration, enabled=enabled)


# ===== 工具函数 =====

def _parse_app_key_or_400(raw, *, required: bool = False) -> Optional[str]:
    """管理端写入：规范化 app_key；非法值 400。空且非 required → None（表示不改）。"""
    if raw is None:
        if required:
            return DEFAULT_APP_KEY
        return None
    s = str(raw).strip()
    if not s:
        return DEFAULT_APP_KEY if required else None
    key = normalize_app_key(s)
    if key not in KNOWN_APP_KEYS:
        raise HTTPException(
            status_code=400,
            detail=_("app_key 必须是 trandsai 或 kaya"),
        )
    return key


def _product_app_key(p: BenefitProductORM) -> str:
    return normalize_app_key(getattr(p, "app_key", None) or DEFAULT_APP_KEY)


def _template_app_key(t: BenefitTemplateORM) -> str:
    return normalize_app_key(getattr(t, "app_key", None) or DEFAULT_APP_KEY)


def _parse_region_from_accept_language(accept_language: Optional[str]) -> str:
    """
    从 Accept-Language / ?lang 解析商店国家码。
    格式示例: zh-CN,zh;q=0.9,en;q=0.8 → CN
              en-US,en;q=0.9 → US
              ja → JP（语种映射，不是 JA）
              无法识别 → ALL
    """
    region = language_to_region(accept_language)
    return region or "ALL"


def _client_language_tag(request: Request) -> str:
    return (request.query_params.get("lang") or request.headers.get("Accept-Language") or "").strip()


_INVALID_COUNTRY_CODES = {"XX", "T1", "ZZ", "A1", "A2", "O1"}


def _normalize_country_code(value: Optional[str]) -> str:
    """标准化国家码，只接受 2 位字母；无法识别返回空串。"""
    code = str(value or "").strip().upper()
    if len(code) == 2 and code.isalpha() and code not in _INVALID_COUNTRY_CODES:
        return code
    return ""


def _extract_country_from_ip_node_headers(request: Request) -> str:
    """优先从网关/边缘节点注入的国家码头部提取地区。"""
    # 常见 CDN / LB / 网关头（参考各家官方约定）
    candidates = [
        "CF-IPCountry",          # Cloudflare
        "X-Vercel-IP-Country",   # Vercel Edge
        "X-Appengine-Country",   # GCP App Engine
        "X-Country-Code",        # Nginx/网关自定义
        "X-Geo-Country",         # 常见自定义
        "X-Region-Country",      # 常见自定义
    ]
    for key in candidates:
        code = _normalize_country_code(request.headers.get(key))
        if code:
            return code
    return ""


def _extract_client_ip(request: Request) -> str:
    """提取客户端 IP（优先 x-forwarded-for 第一个公网候选）。"""
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        # x-forwarded-for: client, proxy1, proxy2
        for part in [x.strip() for x in xff.split(",") if x.strip()]:
            if part and part.lower() != "unknown":
                return part
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return str(request.client.host)
    return ""


def _parse_country_from_ip_text(ip_text: str) -> str:
    """
    对极少数网关把国家信息拼在节点字段里的场景做兜底解析。
    例如: "1.2.3.4|US" / "node=sg" / "country=JP"
    """
    raw = str(ip_text or "").strip()
    if not raw:
        return ""
    m = re.search(r"(?:country|region|cc)\s*[:=]\s*([A-Za-z]{2})", raw, flags=re.I)
    if m:
        return _normalize_country_code(m.group(1))
    m = re.search(r"[|,_/\-\s]([A-Za-z]{2})$", raw)
    if m:
        return _normalize_country_code(m.group(1))
    return ""


def _resolve_region_for_pricing(request: Request) -> tuple[str, str]:
    """
    实时价格 / 模板地区解析优先级：
    0) query `country`（管理端下拉 / 调试显式指定）
    1) X-Storefront-Country（前端商店国家，最准）
    2) Accept-Language / ?lang（ja→JP，不要被 IP 盖掉）
    3) IP/节点国家头
    4) ALL
    返回 (region, source)
    """
    q_country = _normalize_country_code(request.query_params.get("country"))
    if q_country:
        return q_country, "query"

    storefront = _normalize_country_code(
        request.headers.get("X-Storefront-Country")
        or request.headers.get("X-Storefront-Country".lower())
    )
    if storefront:
        return storefront, "storefront"

    lang_region = language_to_region(_client_language_tag(request))
    if lang_region:
        return lang_region, "language"

    country = _extract_country_from_ip_node_headers(request)
    if country:
        return country, "ip_header"

    node_hint = (
        request.headers.get("X-Edge-Node")
        or request.headers.get("X-Request-Node")
        or request.headers.get("X-Pop")
        or ""
    )
    country = _parse_country_from_ip_text(node_hint)
    if country:
        return country, "ip_node"

    client_ip_text = _extract_client_ip(request)
    country = _parse_country_from_ip_text(client_ip_text)
    if country:
        return country, "ip_text"

    return "ALL", "default"


def _product_to_dict(
    p: BenefitProductORM,
    *,
    lang: Optional[str] = None,
    localize: bool = True,
    titles_map: Optional[dict] = None,
    include_titles: bool = False,
    accept_language: Optional[str] = None,
) -> dict:
    """
    商品 ORM → 字典

    参数:
        p: 商品 ORM 对象
        lang: UI 语言（Accept-Language / ?lang），用于 title 本地化兜底
        localize: False 时保留库内原 title（管理后台编辑用）
        titles_map: 国家码 → 展示名；用户端按 Accept-Language 选取
        include_titles: True 时附加 titles（仅管理端）
        accept_language: 原始 Accept-Language / ?lang，用于名称表匹配
    """
    names = titles_map or {}
    if localize:
        picked = pick_title(names, accept_language or lang)
        if picked:
            title = picked
        else:
            ui_lang = normalize_ui_language(lang) if lang else get_ui_lang()
            title = benefit_product_display_name(
                product_type=p.product_type or "",
                period=p.period or "",
                store_product_id=p.store_product_id or "",
                apple_product_id=p.apple_product_id or "",
                title=p.title or "",
                lang=ui_lang,
            )
    else:
        title = p.title or ""
    data = {
        "id": p.id,
        "product_type": p.product_type,
        "platform": p.platform or "all",
        "app_key": _product_app_key(p),
        "title": title,
        "period": p.period or "",
        "price_usd": p.price_usd,
        "price_currency": "",   # 动态填充（Google/Apple 实时价格的币种）
        "price_display": "",    # 动态填充（Google/Apple 返回的本地化价格文本）
        "discount_pct": p.discount_pct or 0,
        "credits_amount": p.credits_amount or 0,
        "daily_gift_credits": p.daily_gift_credits or 0,
        "max_agents": p.max_agents or 0,
        "daily_avg_price": p.daily_avg_price or 0,
        "store_product_id": p.store_product_id or "",
        "apple_product_id": p.apple_product_id or "",
        "sort_order": p.sort_order or 0,
        "is_active": p.is_active,
        "is_limited_offer": bool(getattr(p, "is_limited_offer", False)),
        "limited_duration_sec": _clamp_limited_duration(
            getattr(p, "limited_duration_sec", 0),
            enabled=bool(getattr(p, "is_limited_offer", False)),
        ),
        "extra": json.loads(p.extra) if p.extra else {},
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if include_titles:
        data["titles"] = names
    return data


def _parse_titles_or_400(data: dict) -> Optional[dict]:
    if "titles" not in data:
        return None
    try:
        return normalize_titles_payload(data.get("titles"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_(str(e))) from e


def _template_to_dict(t: BenefitTemplateORM) -> dict:
    """模板 ORM → 字典"""
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description or "",
        "app_key": _template_app_key(t),
        "user_id_rule": json.loads(t.user_id_rule) if t.user_id_rule else None,
        "priority": t.priority or 0,
        "is_active": t.is_active,
        "start_at": t.start_at.isoformat() if t.start_at else None,
        "end_at": t.end_at.isoformat() if t.end_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _match_user_rule(rule_str: str, user_id: Optional[int]) -> bool:
    """检查 user_id_rule 是否匹配当前用户。空规则 = 不限"""
    if not rule_str:
        return True
    if not user_id:
        return False
    try:
        rule = json.loads(rule_str)
    except (json.JSONDecodeError, TypeError):
        return True
    # {"ids": [1, 2, 3]} — 精确匹配
    if "ids" in rule:
        return user_id in rule["ids"]
    # {"min": 1000, "max": 9999} — 范围匹配
    if "min" in rule or "max" in rule:
        if "min" in rule and user_id < rule["min"]:
            return False
        if "max" in rule and user_id > rule["max"]:
            return False
        return True
    return True


def _uniq_templates(templates: List[BenefitTemplateORM]) -> List[BenefitTemplateORM]:
    seen = set()
    out: List[BenefitTemplateORM] = []
    for t in templates:
        if t.id in seen:
            continue
        seen.add(t.id)
        out.append(t)
    return out


def _templates_with_platform(
    db,
    templates: List[BenefitTemplateORM],
    platform: Optional[str],
) -> List[BenefitTemplateORM]:
    """只保留绑了该商店平台（或 platform=all）上架商品的模板。"""
    plat = _normalize_platform_filter(platform)
    if not plat or plat == "all" or not templates:
        return templates
    ids = [t.id for t in templates]
    hit = {
        tid
        for (tid,) in (
            db.query(BenefitTemplateProductORM.template_id)
            .join(BenefitProductORM, BenefitProductORM.id == BenefitTemplateProductORM.product_id)
            .filter(
                BenefitTemplateProductORM.template_id.in_(ids),
                BenefitProductORM.is_active == True,  # noqa: E712
                or_(
                    BenefitProductORM.platform == plat,
                    BenefitProductORM.platform == "all",
                ),
            )
            .distinct()
            .all()
        )
    }
    return [t for t in templates if t.id in hit]


def _templates_with_product_type(
    db,
    templates: List[BenefitTemplateORM],
    product_type: Optional[str],
) -> List[BenefitTemplateORM]:
    if not product_type or not templates:
        return templates
    ids = [t.id for t in templates]
    hit = {
        tid
        for (tid,) in (
            db.query(BenefitTemplateProductORM.template_id)
            .join(BenefitProductORM, BenefitProductORM.id == BenefitTemplateProductORM.product_id)
            .filter(
                BenefitTemplateProductORM.template_id.in_(ids),
                BenefitProductORM.product_type == product_type,
                BenefitProductORM.is_active == True,  # noqa: E712
            )
            .distinct()
            .all()
        )
    }
    return [t for t in templates if t.id in hit]


def _query_active_templates(
    db,
    region_codes: List[str],
    now: datetime,
    *,
    app_key: Optional[str] = None,
) -> List[BenefitTemplateORM]:
    codes = [str(c).upper() for c in region_codes if c]
    if not codes:
        return []
    q = (
        db.query(BenefitTemplateORM)
        .join(BenefitTemplateRegionORM, BenefitTemplateRegionORM.template_id == BenefitTemplateORM.id)
        .filter(
            BenefitTemplateRegionORM.region_code.in_(codes),
            BenefitTemplateORM.is_active == True,  # noqa: E712
            or_(BenefitTemplateORM.start_at == None, BenefitTemplateORM.start_at <= now),  # noqa: E711
            or_(BenefitTemplateORM.end_at == None, BenefitTemplateORM.end_at >= now),  # noqa: E711
        )
    )
    if app_key:
        q = q.filter(BenefitTemplateORM.app_key == app_key)
    rows = q.order_by(BenefitTemplateORM.priority.desc(), BenefitTemplateORM.id.asc()).all()
    return _uniq_templates(rows)


def _select_benefit_templates(
    db,
    region: str,
    now: datetime,
    product_type: Optional[str] = None,
    *,
    app_key: Optional[str] = None,
    platform: Optional[str] = None,
) -> List[BenefitTemplateORM]:
    """本地区 + 应用 + 平台有模板才用本地；没有才回落 ALL。同优先级合并。"""
    region = (region or "ALL").upper()
    key = normalize_app_key(app_key)

    def _pick(codes: List[str]) -> List[BenefitTemplateORM]:
        rows = _query_active_templates(db, codes, now, app_key=key)
        rows = _templates_with_product_type(db, rows, product_type)
        return _templates_with_platform(db, rows, platform)

    exact = []
    if region and region != "ALL":
        exact = _pick([region])
    chosen = exact or _pick(["ALL"])
    if not chosen:
        return []
    top_priority = chosen[0].priority or 0
    return [t for t in chosen if (t.priority or 0) == top_priority]


def _user_has_store_purchase(db, user_id: Optional[int]) -> bool:
    """是否已在 App 内付费成功（充值或订阅），用于限时优惠仅面向首充。"""
    if not user_id:
        return False
    from core.database import RechargeRecordORM, VipSubscriptionORM

    if (
        db.query(RechargeRecordORM.id)
        .filter(
            RechargeRecordORM.user_id == int(user_id),
            RechargeRecordORM.store_type != "system",
        )
        .first()
    ):
        return True
    vip = (
        db.query(VipSubscriptionORM.id)
        .filter(
            VipSubscriptionORM.user_id == int(user_id),
            VipSubscriptionORM.status != "refunded",
        )
        .first()
    )
    return bool(vip)


def _optional_user_id(request: Request) -> Optional[int]:
    raw = (request.headers.get("x-token") or request.headers.get("X-Token") or "").strip()
    auth = (request.headers.get("Authorization") or "").strip()
    if not raw and auth.lower().startswith("bearer "):
        raw = auth[7:].strip()
    if not raw:
        return None
    try:
        from core.auth import verify_token

        uid = verify_token(raw)
        return int(uid) if uid else None
    except Exception:
        return None


def _apply_template_user_rule(
    templates: List[BenefitTemplateORM],
    request: Request,
) -> List[BenefitTemplateORM]:
    """有 user_id_rule 的模板仅对命中用户生效；没命中则回落到无规则模板。"""
    if not templates:
        return templates
    uid = _optional_user_id(request)
    targeted = [t for t in templates if (t.user_id_rule or "").strip()]
    generic = [t for t in templates if not (t.user_id_rule or "").strip()]
    hit = [t for t in targeted if _match_user_rule(t.user_id_rule, uid)]
    return hit or generic


# =============================================================================
#  前端查询接口（公开）
# =============================================================================


_COUNTRY_CURRENCY: dict[str, tuple[str, str]] = {
    "US": ("USD", "$"), "CA": ("CAD", "CA$"), "GB": ("GBP", "£"),
    "AU": ("AUD", "A$"), "NZ": ("NZD", "NZ$"),
    "EU": ("EUR", "€"), "DE": ("EUR", "€"), "FR": ("EUR", "€"),
    "IT": ("EUR", "€"), "ES": ("EUR", "€"), "NL": ("EUR", "€"),
    "BE": ("EUR", "€"), "AT": ("EUR", "€"), "IE": ("EUR", "€"),
    "FI": ("EUR", "€"), "PT": ("EUR", "€"), "GR": ("EUR", "€"),
    "JP": ("JPY", "¥"), "CN": ("CNY", "¥"), "KR": ("KRW", "₩"),
    "TW": ("TWD", "NT$"), "HK": ("HKD", "HK$"), "SG": ("SGD", "S$"),
    "IN": ("INR", "₹"), "ID": ("IDR", "Rp"), "MY": ("MYR", "RM"),
    "TH": ("THB", "฿"), "PH": ("PHP", "₱"), "VN": ("VND", "₫"),
    "BR": ("BRL", "R$"), "MX": ("MXN", "MX$"),
    "RU": ("RUB", "₽"), "TR": ("TRY", "₺"), "BG": ("BGN", "лв"),
    "SA": ("SAR", "SAR"), "AE": ("AED", "AED"), "IR": ("IRR", "﷼"),
    "ZA": ("ZAR", "R"), "NG": ("NGN", "₦"),
    "SE": ("SEK", "kr"), "NO": ("NOK", "kr"), "DK": ("DKK", "kr"),
    "PL": ("PLN", "zł"), "CZ": ("CZK", "Kč"), "CH": ("CHF", "CHF"),
    "IL": ("ILS", "₪"), "EG": ("EGP", "E£"),
    "CL": ("CLP", "CL$"), "CO": ("COP", "COL$"), "AR": ("ARS", "ARS"),
    "PK": ("PKR", "Rs"), "BD": ("BDT", "৳"),
}

# 管理端国家下拉（与 app_key 下拉同形态）；value=ISO 国家码
_COUNTRY_OPTIONS: list[dict[str, str]] = [
    {"value": "US", "label": "美国 / US"},
    {"value": "JP", "label": "日本 / JP"},
    {"value": "KR", "label": "韩国 / KR"},
    {"value": "CN", "label": "中国 / CN"},
    {"value": "TW", "label": "台湾 / TW"},
    {"value": "HK", "label": "香港 / HK"},
    {"value": "SG", "label": "新加坡 / SG"},
    {"value": "GB", "label": "英国 / GB"},
    {"value": "DE", "label": "德国 / DE"},
    {"value": "FR", "label": "法国 / FR"},
    {"value": "IT", "label": "意大利 / IT"},
    {"value": "ES", "label": "西班牙 / ES"},
    {"value": "NL", "label": "荷兰 / NL"},
    {"value": "PT", "label": "葡萄牙 / PT"},
    {"value": "BR", "label": "巴西 / BR"},
    {"value": "MX", "label": "墨西哥 / MX"},
    {"value": "CA", "label": "加拿大 / CA"},
    {"value": "AU", "label": "澳大利亚 / AU"},
    {"value": "NZ", "label": "新西兰 / NZ"},
    {"value": "ID", "label": "印尼 / ID"},
    {"value": "MY", "label": "马来西亚 / MY"},
    {"value": "TH", "label": "泰国 / TH"},
    {"value": "PH", "label": "菲律宾 / PH"},
    {"value": "VN", "label": "越南 / VN"},
    {"value": "IN", "label": "印度 / IN"},
    {"value": "RU", "label": "俄罗斯 / RU"},
    {"value": "TR", "label": "土耳其 / TR"},
    {"value": "SA", "label": "沙特 / SA"},
    {"value": "AE", "label": "阿联酋 / AE"},
    {"value": "IR", "label": "伊朗 / IR"},
    {"value": "BG", "label": "保加利亚 / BG"},
    {"value": "PL", "label": "波兰 / PL"},
    {"value": "SE", "label": "瑞典 / SE"},
    {"value": "CH", "label": "瑞士 / CH"},
]


def country_meta() -> dict:
    """管理端国家下拉 meta（对齐 apps）。"""
    return {
        "param": "country",
        "default_source": "Accept-Language → 商店国家码（如 ja→JP）；无法识别则 US",
        "countries": list(_COUNTRY_OPTIONS),
    }


def _resolve_country_param(
    country: Optional[str],
    request: Optional[Request] = None,
) -> str:
    """解析管理端/预览用国家码。

    优先 query `country`；否则 Accept-Language / ?lang（ja→JP）；仍无则 US。
    """
    code = _normalize_country_code(country)
    if code:
        return code
    if request is not None:
        lang = (
            (request.query_params.get("lang") or "").strip()
            or (request.headers.get("Accept-Language") or "").strip()
        )
        mapped = language_to_region(lang)
        if mapped:
            return mapped
    return "US"


def _format_price_display(amount: float, currency: str, symbol: str) -> str:
    if currency in ("JPY", "KRW", "VND", "CLP", "COP", "IDR"):
        return f"{symbol}{int(round(amount))}"
    return f"{symbol}{amount:.2f}"


def _apply_live_price(product_dict: dict, region_code: str) -> None:
    """填充 price_currency / price_display。优先实时 → 已同步本地价 → USD 回退。"""
    platform = product_dict.get("platform", "all")
    store_id = product_dict.get("store_product_id", "")
    apple_id = product_dict.get("apple_product_id", "")
    product_type = product_dict.get("product_type", "subscribe")

    resolved_currency = ""
    resolved_price = 0.0
    resolved_display = ""

    # 1) Google Play real-time
    if store_id and platform in ("google", "all"):
        try:
            from services.payment_verify import get_google_product_price
            price_info = get_google_product_price(store_id, region_code, product_type=product_type)
            if price_info and price_info.get("price_micros"):
                micros = price_info["price_micros"]
                resolved_price = round(micros / 1_000_000, 2)
                resolved_currency = price_info.get("currency", "USD")
                resolved_display = price_info.get("price_display", "")
                product_dict["price_usd"] = resolved_price
        except Exception as e:
            logger.warning("[benefits] Google price fetch failed for %s: %s", store_id, e)

    # 2) Apple real-time
    if not resolved_currency and apple_id and platform in ("apple", "all"):
        try:
            from services.payment_verify import get_apple_product_price
            price_info = get_apple_product_price(apple_id, region_code)
            if price_info and price_info.get("price_micros"):
                micros = price_info["price_micros"]
                resolved_price = round(micros / 1_000_000, 2)
                resolved_currency = price_info.get("currency", "USD")
                resolved_display = price_info.get("price_display", "")
                product_dict["price_usd"] = resolved_price
        except Exception as e:
            logger.warning("[benefits] Apple price fetch failed for %s: %s", apple_id, e)

    # 3) Synced local price from ProductPriceORM
    if not resolved_currency:
        product_id = product_dict.get("id")
        if product_id and region_code and region_code != "ALL":
            try:
                from services.appstore_service import get_local_price
                local = get_local_price(product_id, region_code)
                if local and local.get("price"):
                    resolved_currency = local["currency"]
                    resolved_price = float(local["price"])
            except Exception as e:
                logger.warning("[benefits] local price lookup failed for product %s: %s", product_id, e)

    # 4) Resolve final price_currency / price_display
    cc = region_code.upper() if region_code else ""
    country_cur, country_sym = _COUNTRY_CURRENCY.get(cc, ("USD", "$"))

    if resolved_currency:
        final_cur = resolved_currency
        final_price = resolved_price
    else:
        final_cur = "USD"
        country_sym = "$"
        final_price = float(product_dict.get("price_usd") or 0)
    product_dict["price_currency"] = final_cur

    # Look up symbol from resolved_currency if it differs from country default
    if resolved_currency and resolved_currency != country_cur:
        # Find symbol for resolved_currency by reverse lookup
        final_sym = country_sym
        for _cc, (_c, _s) in _COUNTRY_CURRENCY.items():
            if _c == resolved_currency:
                final_sym = _s
                break
    else:
        final_sym = country_sym

    # Use resolved_display only if it already contains a currency symbol
    _stripped = (resolved_display or "").replace(",", "").replace(".", "").replace(" ", "")
    if resolved_display and _stripped and not _stripped.isdigit():
        product_dict["price_display"] = resolved_display
    else:
        product_dict["price_display"] = _format_price_display(final_price, final_cur, final_sym)




@router.get("/api/benefits", summary="获取当前区域的福利信息（前端）")
async def get_benefits(
    request: Request,
    type: Optional[str] = Query(None, description="筛选类型：subscribe / point"),
    platform: Optional[str] = Query(None, description="必填：apple / google / web"),
    country: Optional[str] = Query(
        None,
        description="商店国家码（可选）；不传则 X-Storefront-Country → Accept-Language → IP",
    ),
):
    """
    按区域返回福利商品，按 subscribe / point 分组。
    精确国家模板优先；没有该国（或该 type 下没有）才用 ALL。
    地区判定：X-Storefront-Country > Accept-Language/?lang（ja→JP）> IP/节点国家。
    应用：请求头 X-App-Key（缺省 trandsai）；只返回同 app_key 的模板与商品。
    Query platform 必填 apple / google / web。
    """
    platform = _require_store_platform(platform)
    accept_language = request.headers.get("Accept-Language")
    language_tag = _client_language_tag(request)
    ui_lang = normalize_ui_language(language_tag)
    region, region_source = _resolve_region_for_pricing(request)
    app_key = app_key_from_headers(request.headers)
    # logger.info( 钱包日志
    #     "[benefits] incoming query=%s headers=%s",
    #     dict(request.query_params),
    #     {
    #         "X-Storefront-Country": request.headers.get("X-Storefront-Country"),
    #         "X-Country-Code": request.headers.get("X-Country-Code"),
    #         "Accept-Language": request.headers.get("Accept-Language"),
    #         "X-App-Key": request.headers.get("X-App-Key"),
    #         "X-Language": request.headers.get("X-Language"),
    #     },
    # )
    # logger.info(
    #     "[benefits] app_key=%s region=%s source=%s storefront=%s accept_language=%s ui_lang=%s client_ip=%s",
    #     app_key,
    #     region,
    #     region_source,
    #     request.headers.get("X-Storefront-Country"),
    #     accept_language,
    #     ui_lang,
    #     _extract_client_ip(request),
    # )

    now = datetime.now(timezone.utc)

    with get_db() as db:
        templates = _select_benefit_templates(
            db, region, now, product_type=type, app_key=app_key, platform=platform
        )
        templates = _apply_template_user_rule(templates, request)
        if not templates:
            return {"subscribe": [], "point": []}

        template_ids = [t.id for t in templates]

        # 4. 获取这些模板关联的商品
        product_ids = (
            db.query(BenefitTemplateProductORM.product_id)
            .filter(BenefitTemplateProductORM.template_id.in_(template_ids))
            .distinct()
            .all()
        )
        product_ids = [pid for (pid,) in product_ids]

        if not product_ids:
            return {"subscribe": [], "point": []}

        query = (
            db.query(BenefitProductORM)
            .filter(
                BenefitProductORM.id.in_(product_ids),
                BenefitProductORM.is_active == True,  # noqa: E712
                BenefitProductORM.app_key == app_key,
            )
        )
        if type:
            query = query.filter(BenefitProductORM.product_type == type)
        query = _apply_product_platform_filter(query, platform)
        products = query.order_by(*_product_display_order(platform)).all()
        titles_by_id = load_titles_maps(db, [p.id for p in products])
        title_locale = language_tag

        # 5. 分组返回（在 session 内完成转换，避免 DetachedInstanceError）
        result = {"subscribe": [], "point": []}
        for p in products:
            if getattr(p, "is_limited_offer", False):
                continue
            key = p.product_type
            if key in result:
                d = _product_to_dict(
                    p,
                    lang=ui_lang,
                    localize=True,
                    titles_map=titles_by_id.get(p.id),
                    accept_language=title_locale,
                )
                # 尝试获取 Google/Apple 实时价格，覆盖 price_usd
                _apply_live_price(d, region)
                result[key].append(d)

    return result


def _normalize_platform_filter(platform: Optional[str]) -> Optional[str]:
    p = (platform or "").strip().lower()
    if p in ("apple", "google", "web", "all"):
        return p
    return None


def _require_store_platform(platform: Optional[str]) -> str:
    plat = _normalize_platform_filter(platform)
    if plat not in ("apple", "google", "web"):
        raise HTTPException(status_code=400, detail=_("platform 必须是 apple / google / web"))
    return plat


def _apply_product_platform_filter(query, platform: Optional[str]):
    plat = _normalize_platform_filter(platform)
    if plat and plat != "all":
        return query.filter(
            or_(
                BenefitProductORM.platform == plat,
                BenefitProductORM.platform == "all",
            )
        )
    return query


def _product_display_order(platform: Optional[str] = None):
    """sort_order 越大越靠前；指定平台时本平台优先于 all。"""
    plat = _normalize_platform_filter(platform)
    clauses = []
    if plat and plat != "all":
        clauses.append(case((BenefitProductORM.platform == plat, 0), else_=1))
    clauses.append(BenefitProductORM.sort_order.desc())
    clauses.append(BenefitProductORM.id.asc())
    return clauses


def _product_ids_in_region_templates(db, region: str, app_key: Optional[str] = None) -> list:
    code = _normalize_country_code(region)
    if not code:
        return []
    tq = (
        db.query(BenefitTemplateORM.id)
        .join(
            BenefitTemplateRegionORM,
            BenefitTemplateRegionORM.template_id == BenefitTemplateORM.id,
        )
        .filter(BenefitTemplateRegionORM.region_code == code)
    )
    if app_key:
        tq = tq.filter(BenefitTemplateORM.app_key == app_key)
    tids = [i for (i,) in tq.distinct().all()]
    if not tids:
        return []
    rows = (
        db.query(BenefitTemplateProductORM.product_id)
        .filter(BenefitTemplateProductORM.template_id.in_(tids))
        .distinct()
        .all()
    )
    return [int(pid) for (pid,) in rows if pid is not None]


def _empty_limited_status(**extra) -> dict:
    body = {
        "available": False,
        "eligible": False,
        "show": False,
        "first_paywall": False,
        "started": False,
        "reason": "no_product",
        "duration_sec": 0,
        "remaining_sec": 0,
        "expires_at": None,
        "product_id": None,
        "product": None,
    }
    body.update(extra)
    return body


def _load_limited_product(request: Request, platform: Optional[str]):
    language_tag = _client_language_tag(request)
    ui_lang = normalize_ui_language(language_tag)
    region, _region_source = _resolve_region_for_pricing(request)
    app_key = app_key_from_headers(request.headers)
    now = datetime.now(timezone.utc)
    with get_db() as db:
        templates = _select_benefit_templates(
            db, region, now, product_type=None, app_key=app_key, platform=platform
        )
        templates = _apply_template_user_rule(templates, request)
        if not templates:
            return None
        template_ids = [t.id for t in templates]
        product_ids = (
            db.query(BenefitTemplateProductORM.product_id)
            .filter(BenefitTemplateProductORM.template_id.in_(template_ids))
            .distinct()
            .all()
        )
        product_ids = [pid for (pid,) in product_ids]
        if not product_ids:
            return None
        query = (
            db.query(BenefitProductORM)
            .filter(
                BenefitProductORM.id.in_(product_ids),
                BenefitProductORM.is_active == True,  # noqa: E712
                BenefitProductORM.app_key == app_key,
                BenefitProductORM.is_limited_offer == True,  # noqa: E712
            )
        )
        query = _apply_product_platform_filter(query, platform)
        product = query.order_by(*_product_display_order(platform)).first()
        if not product:
            return None
        product_id = int(product.id)
        titles_by_id = load_titles_maps(db, [product_id])
        d = _product_to_dict(
            product,
            lang=ui_lang,
            localize=True,
            titles_map=titles_by_id.get(product_id),
            accept_language=language_tag,
        )
        _apply_live_price(d, region)
        duration = int(d.get("limited_duration_sec") or LIMITED_DURATION_DEFAULT)
        return product_id, d, duration, app_key, region


def _limited_offer_status_payload(
    request: Request,
    *,
    platform: Optional[str],
    user_id: Optional[int],
) -> dict:
    from services.limited_offer import (
        ensure_started,
        load_offer,
        mark_limited_offer_used,
        status_flags,
    )

    loaded = _load_limited_product(request, platform)
    if not loaded:
        return _empty_limited_status()
    product_id, d, duration, app_key, _region = loaded
    with get_db() as db:
        paid = _user_has_store_purchase(db, user_id)
        if paid and user_id:
            mark_limited_offer_used(db, user_id)

    if user_id and not paid:
        ensure_started(
            user_id=user_id,
            app_key=app_key,
            product_id=int(product_id),
            duration_sec=duration,
        )
    row = load_offer(user_id, app_key) if user_id else None
    flags = status_flags(row, paid=paid)
    eligible = bool(user_id) and not paid
    if not user_id:
        reason = "no_user"
    elif paid:
        reason = "already_paid"
    elif flags.get("session_reason") == "expired":
        reason = "expired"
    elif flags["show"] and eligible:
        reason = ""
    else:
        reason = flags.get("session_reason") or ""
    show = bool(eligible and flags["show"])
    return {
        "available": True,
        "eligible": eligible,
        "show": show,
        "first_paywall": flags["first_paywall"],
        "started": flags["started"] and eligible,
        "reason": reason,
        "duration_sec": duration,
        "remaining_sec": flags["remaining_sec"] if show else 0,
        "expires_at": flags["expires_at"] if show else None,
        "product_id": int(product_id),
        "product": d,
    }


@router.get("/api/benefits/limited-offer", summary="限时优惠：首次开始倒计时，之后只返回状态")
async def get_limited_offer(
    request: Request,
    platform: Optional[str] = Query(None, description="必填：apple / google / web"),
    country: Optional[str] = Query(
        None,
        description="商店国家码（可选）；不传则 X-Storefront-Country → Accept-Language → IP",
    ),
):
    """限时商品 + 倒计时。同一接口：首次 GET 开始计时，之后 GET 只返回剩余时间和 show（不重置）。"""
    token = (request.headers.get("x-token") or request.headers.get("X-Token") or "").strip()
    incoming = {
        "query": dict(request.query_params),
        "platform": platform,
        "country": country,
        "headers": {
            "X-App-Key": request.headers.get("X-App-Key") or request.headers.get("x-app-key"),
            "X-Storefront-Country": request.headers.get("X-Storefront-Country"),
            "X-Country-Code": request.headers.get("X-Country-Code"),
            "Accept-Language": request.headers.get("Accept-Language"),
            "X-Language": request.headers.get("X-Language"),
            "has_token": bool(token),
        },
    }
    logger.info("[limited-offer] request %s", json.dumps(incoming, ensure_ascii=False, default=str))
    platform = _require_store_platform(platform)
    user_id = _optional_user_id(request)
    payload = _limited_offer_status_payload(request, platform=platform, user_id=user_id)
    logger.info(
        "[limited-offer] response user_id=%s platform=%s body=%s",
        user_id,
        platform,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    return payload


# =============================================================================
#  后台管理 — 福利商品 CRUD
# =============================================================================

@router.get("/api/admin/benefits/products", summary="商品列表（管理后台）")
async def admin_list_products(
    request: Request,
    _token: str = Depends(admin_auth_required),
    product_type: Optional[str] = Query(None, description="筛选：subscribe / point"),
    is_active: Optional[bool] = Query(None, description="筛选：是否上架"),
    app_key: Optional[str] = Query(None, description="筛选应用：trandsai / kaya；空=全部"),
    country: Optional[str] = Query(
        None,
        description="US/JP/FR…；传入则只返回该地区模板绑定的商品，并按该国展示价格",
    ),
    is_limited_offer: Optional[bool] = Query(None, description="筛选限时优惠商品"),
):
    app_filter = resolve_app_key_filter(app_key)
    region = _resolve_country_param(country, request)
    explicit_region = _normalize_country_code(country)
    with get_db() as db:
        q = db.query(BenefitProductORM)
        if product_type:
            q = q.filter(BenefitProductORM.product_type == product_type)
        if is_active is not None:
            q = q.filter(BenefitProductORM.is_active == is_active)
        if app_filter:
            q = q.filter(BenefitProductORM.app_key == app_filter)
        if is_limited_offer is not None:
            q = q.filter(BenefitProductORM.is_limited_offer == bool(is_limited_offer))
        if explicit_region:
            bound_ids = _product_ids_in_region_templates(db, explicit_region, app_filter)
            if not bound_ids:
                products = []
            else:
                q = q.filter(BenefitProductORM.id.in_(bound_ids))
                products = q.order_by(*_product_display_order()).all()
        else:
            products = q.order_by(*_product_display_order()).all()
        titles_by_id = load_titles_maps(db, [p.id for p in products])
        items = []
        for p in products:
            d = _product_to_dict(
                p,
                localize=False,
                titles_map=titles_by_id.get(p.id),
                include_titles=True,
            )
            # 价格只读：按国家拉商店价，填充 price_display / price_currency
            _apply_live_price(d, region)
            items.append(d)
    return {
        "items": items,
        "total": len(items),
        "apps": app_key_meta()["apps"],
        "country": region,
        "countries": country_meta()["countries"],
    }


@router.post("/api/admin/benefits/products", summary="创建商品（管理后台）")
async def admin_create_product(
    data: dict = Body(...),
    _token: str = Depends(admin_auth_required),
):
    """
    body 示例：
    {
        "product_type": "subscribe",
        "platform": "apple",  // apple / google / web / all
        "app_key": "trandsai",  // trandsai / kaya；缺省 trandsai
        "title": "年度VIP",
        "period": "yearly",
        "discount_pct": 40,
        "daily_gift_credits": 50,
        "max_agents": 5,
        "daily_avg_price": 0.23,
        "store_product_id": "com.app.vip.yearly",  // Google Play 商品 ID
        "apple_product_id": "com.app.vip.yearly.apple",  // App Store 商品 ID
        "sort_order": 1,
        "is_limited_offer": false,
        "limited_duration_sec": 1800,
        "titles": {"US": "Yearly VIP", "JP": "年間VIP"}
    }
    价格由应用商店实时决定，勿传 price_usd（传了也会忽略，库内默认 0）。
    """
    product_type = data.get("product_type")
    if product_type not in ("subscribe", "point"):
        raise HTTPException(status_code=400, detail=_("product_type 必须是 subscribe 或 point"))
    platform = data.get("platform", "all")
    if platform not in ("apple", "google", "web", "all"):
        raise HTTPException(status_code=400, detail=_("platform 必须是 apple / google / web / all"))
    if not data.get("title"):
        raise HTTPException(status_code=400, detail=_("title 不能为空"))
    titles_map = _parse_titles_or_400(data)
    product_app_key = _parse_app_key_or_400(data.get("app_key"), required=True)
    limited_on, limited_sec = _parse_limited_fields(data)
    # 价格不自定义：库字段仅作兜底，默认 0；展示走商店实时价
    price_usd = 0.0

    with get_db() as db:
        product = BenefitProductORM(
            product_type=product_type,
            platform=platform,
            app_key=product_app_key,
            title=data["title"],
            period=data.get("period", ""),
            price_usd=price_usd,
            discount_pct=int(data.get("discount_pct", 0)),
            credits_amount=int(data.get("credits_amount", 0)),
            daily_gift_credits=int(data.get("daily_gift_credits", 0)),
            max_agents=int(data.get("max_agents", 0)),
            daily_avg_price=float(data.get("daily_avg_price", 0)),
            store_product_id=data.get("store_product_id", ""),
            apple_product_id=data.get("apple_product_id", ""),
            sort_order=int(data.get("sort_order", 0)),
            is_active=data.get("is_active", True),
            is_limited_offer=limited_on,
            limited_duration_sec=limited_sec,
            extra=json.dumps(data.get("extra", {}), ensure_ascii=False),
        )
        db.add(product)
        db.flush()
        product_id = product.id
        if titles_map is not None:
            replace_product_titles(db, product_id, titles_map)

    return {"ok": True, "id": product_id}


@router.put("/api/admin/benefits/products/{product_id}", summary="更新商品（管理后台）")
async def admin_update_product(
    product_id: int,
    data: dict = Body(...),
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        product = db.query(BenefitProductORM).filter(BenefitProductORM.id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=_("商品不存在"))

        # 可更新字段白名单（不含 price_usd：价格只读，由商店决定）
        allowed = {
            "title", "period", "discount_pct", "credits_amount",
            "daily_gift_credits", "max_agents", "daily_avg_price",
            "platform", "store_product_id", "apple_product_id",
            "sort_order", "is_active", "product_type", "extra", "app_key",
        }
        for key, val in data.items():
            if key not in allowed:
                continue
            if key == "platform":
                val = (val or "all").strip().lower()
                if val not in ("apple", "google", "web", "all"):
                    raise HTTPException(
                        status_code=400,
                        detail=_("platform 必须是 apple / google / web / all"),
                    )
            if key == "app_key":
                val = _parse_app_key_or_400(val, required=True)
            elif key == "extra" and isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            elif key in ("discount_pct", "credits_amount", "daily_gift_credits",
                         "max_agents", "sort_order"):
                val = int(val)
            elif key == "daily_avg_price":
                val = float(val)
            elif key == "is_active":
                val = bool(val)
            setattr(product, key, val)

        if "is_limited_offer" in data or "limited_duration_sec" in data:
            limited_on, limited_sec = _parse_limited_fields(data, existing=product)
            product.is_limited_offer = limited_on
            product.limited_duration_sec = limited_sec

        if "titles" in data:
            replace_product_titles(db, product_id, _parse_titles_or_400(data) or {})

    return {"ok": True}


@router.delete("/api/admin/benefits/products/{product_id}", summary="删除商品（管理后台）")
async def admin_delete_product(
    product_id: int,
    _token: str = Depends(admin_auth_required),
):
    """真正删除商品行。仍绑定模板时拒绝，需先解绑。"""
    with get_db() as db:
        product = db.query(BenefitProductORM).filter(BenefitProductORM.id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=_("商品不存在"))
        bound = (
            db.query(BenefitTemplateORM.id, BenefitTemplateORM.name)
            .join(
                BenefitTemplateProductORM,
                BenefitTemplateProductORM.template_id == BenefitTemplateORM.id,
            )
            .filter(BenefitTemplateProductORM.product_id == product_id)
            .all()
        )
        if bound:
            shown = "、".join(f"#{i} {n}".strip() for i, n in bound[:8])
            raise HTTPException(
                status_code=400,
                detail=_("商品仍绑定模板，不能删除：") + shown,
            )
        db.query(BenefitProductTitleORM).filter(
            BenefitProductTitleORM.product_id == product_id
        ).delete(synchronize_session=False)
        try:
            from core.database import ProductPriceORM

            db.query(ProductPriceORM).filter(
                ProductPriceORM.product_id == product_id
            ).delete(synchronize_session=False)
        except Exception:
            logger.debug("[benefits] skip product prices cleanup id=%s", product_id, exc_info=True)
        db.delete(product)
    return {"ok": True}


# =============================================================================
#  后台管理 — 福利模板 CRUD
# =============================================================================

@router.get("/api/admin/benefits/templates", summary="模板列表（管理后台）")
async def admin_list_templates(
    _token: str = Depends(admin_auth_required),
    is_active: Optional[bool] = Query(None, description="筛选：是否启用"),
    app_key: Optional[str] = Query(None, description="筛选应用：trandsai / kaya；空=全部"),
    country: Optional[str] = Query(None, description="筛选地区：US/JP/FR；只返回 regions 含该码的模板"),
    region: Optional[str] = Query(None, description="同 country"),
):
    app_filter = resolve_app_key_filter(app_key)
    region_code = _normalize_country_code(country) or _normalize_country_code(region)
    with get_db() as db:
        q = db.query(BenefitTemplateORM)
        if is_active is not None:
            q = q.filter(BenefitTemplateORM.is_active == is_active)
        if app_filter:
            q = q.filter(BenefitTemplateORM.app_key == app_filter)
        if region_code:
            q = (
                q.join(
                    BenefitTemplateRegionORM,
                    BenefitTemplateRegionORM.template_id == BenefitTemplateORM.id,
                )
                .filter(BenefitTemplateRegionORM.region_code == region_code)
                .distinct()
            )
        templates = q.order_by(BenefitTemplateORM.priority.desc(), BenefitTemplateORM.id).all()

        result = []
        for t in templates:
            td = _template_to_dict(t)
            # 附带关联的商品 ID 列表
            product_ids = (
                db.query(BenefitTemplateProductORM.product_id)
                .filter(BenefitTemplateProductORM.template_id == t.id)
                .all()
            )
            td["product_ids"] = [pid for (pid,) in product_ids]
            # 附带关联的区域列表
            regions = (
                db.query(BenefitTemplateRegionORM.region_code)
                .filter(BenefitTemplateRegionORM.template_id == t.id)
                .all()
            )
            td["regions"] = [r for (r,) in regions]
            result.append(td)

    return {
        "items": result,
        "total": len(result),
        "apps": app_key_meta()["apps"],
        "country": region_code or None,
        "countries": country_meta()["countries"],
    }


@router.post("/api/admin/benefits/templates", summary="创建模板（管理后台）")
async def admin_create_template(
    data: dict = Body(...),
    _token: str = Depends(admin_auth_required),
):
    """
    body 示例：
    {
        "name": "US-年度促销",
        "description": "美国区域年度订阅促销模板",
        "app_key": "trandsai",
        "user_id_rule": {"min": 1000, "max": 99999},
        "priority": 10,
        "start_at": "2026-01-01T00:00:00Z",
        "end_at": null,
        "product_ids": [1, 2, 5],
        "regions": ["US", "ALL"]
    }
    """
    if not data.get("name"):
        raise HTTPException(status_code=400, detail=_("name 不能为空"))

    product_ids = data.pop("product_ids", [])
    regions = data.pop("regions", [])
    template_app_key = _parse_app_key_or_400(data.get("app_key"), required=True)

    with get_db() as db:
        # 解析 user_id_rule
        rule = data.get("user_id_rule")
        rule_str = ""
        if rule:
            rule_str = json.dumps(rule, ensure_ascii=False) if isinstance(rule, dict) else str(rule)

        # 解析时间
        def _parse_dt(val):
            if not val:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        template = BenefitTemplateORM(
            name=data["name"],
            description=data.get("description", ""),
            app_key=template_app_key,
            user_id_rule=rule_str,
            priority=int(data.get("priority", 0)),
            is_active=data.get("is_active", True),
            start_at=_parse_dt(data.get("start_at")),
            end_at=_parse_dt(data.get("end_at")),
        )
        db.add(template)
        db.flush()
        template_id = template.id

        # 绑定商品
        for pid in product_ids:
            db.add(BenefitTemplateProductORM(template_id=template_id, product_id=int(pid)))
        # 绑定区域
        for region in regions:
            db.add(BenefitTemplateRegionORM(template_id=template_id, region_code=str(region).upper()))

    return {"ok": True, "id": template_id}


@router.put("/api/admin/benefits/templates/{template_id}", summary="更新模板（管理后台）")
async def admin_update_template(
    template_id: int,
    data: dict = Body(...),
    _token: str = Depends(admin_auth_required),
):
    """
    body 示例（仅传需要修改的字段）：
    {
        "name": "新名称",
        "priority": 20,
        "product_ids": [1, 3, 6],
        "regions": ["US", "EU"]
    }
    """
    with get_db() as db:
        template = db.query(BenefitTemplateORM).filter(BenefitTemplateORM.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail=_("模板不存在"))

        # 基础字段更新
        allowed = {"name", "description", "priority", "is_active", "app_key"}
        for key in allowed:
            if key not in data:
                continue
            if key == "app_key":
                setattr(template, key, _parse_app_key_or_400(data[key], required=True))
            else:
                setattr(template, key, data[key])

        if "user_id_rule" in data:
            rule = data["user_id_rule"]
            template.user_id_rule = json.dumps(rule, ensure_ascii=False) if isinstance(rule, dict) else (str(rule) if rule else "")

        def _parse_dt(val):
            if not val:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        if "start_at" in data:
            template.start_at = _parse_dt(data["start_at"])
        if "end_at" in data:
            template.end_at = _parse_dt(data["end_at"])

        # 更新关联商品（全量替换）
        if "product_ids" in data:
            db.query(BenefitTemplateProductORM).filter(
                BenefitTemplateProductORM.template_id == template_id
            ).delete()
            for pid in data["product_ids"]:
                db.add(BenefitTemplateProductORM(template_id=template_id, product_id=int(pid)))

        # 更新关联区域（全量替换）
        if "regions" in data:
            db.query(BenefitTemplateRegionORM).filter(
                BenefitTemplateRegionORM.template_id == template_id
            ).delete()
            for region in data["regions"]:
                db.add(BenefitTemplateRegionORM(template_id=template_id, region_code=str(region).upper()))

    return {"ok": True}


@router.delete("/api/admin/benefits/templates/{template_id}", summary="删除模板（管理后台）")
async def admin_delete_template(
    template_id: int,
    _token: str = Depends(admin_auth_required),
):
    """真正删除模板行。只解绑关联的商品/区域，不删除商品本身。"""
    with get_db() as db:
        template = db.query(BenefitTemplateORM).filter(BenefitTemplateORM.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail=_("模板不存在"))
        # 解绑商品（保留 benefit_products）
        db.query(BenefitTemplateProductORM).filter(
            BenefitTemplateProductORM.template_id == template_id
        ).delete(synchronize_session=False)
        # 解绑区域
        db.query(BenefitTemplateRegionORM).filter(
            BenefitTemplateRegionORM.template_id == template_id
        ).delete(synchronize_session=False)
        db.delete(template)
    return {"ok": True}


# =============================================================================
#  后台管理 — 模板绑定商品（细粒度操作）
# =============================================================================

@router.post("/api/admin/benefits/templates/{template_id}/products", summary="绑定商品到模板")
async def admin_bind_products(
    template_id: int,
    data: dict = Body(..., description='{"product_ids": [1, 2, 3]}'),
    _token: str = Depends(admin_auth_required),
):
    """追加绑定（不覆盖已有的）"""
    product_ids = data.get("product_ids", [])
    if not product_ids:
        raise HTTPException(status_code=400, detail=_("product_ids 不能为空"))

    with get_db() as db:
        template = db.query(BenefitTemplateORM).filter(BenefitTemplateORM.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail=_("模板不存在"))

        existing = set(
            pid for (pid,) in
            db.query(BenefitTemplateProductORM.product_id)
            .filter(BenefitTemplateProductORM.template_id == template_id)
            .all()
        )
        added = 0
        for pid in product_ids:
            pid = int(pid)
            if pid not in existing:
                db.add(BenefitTemplateProductORM(template_id=template_id, product_id=pid))
                added += 1

    return {"ok": True, "added": added}


@router.delete("/api/admin/benefits/templates/{template_id}/products/{product_id}", summary="解绑商品")
async def admin_unbind_product(
    template_id: int,
    product_id: int,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        deleted = (
            db.query(BenefitTemplateProductORM)
            .filter(
                BenefitTemplateProductORM.template_id == template_id,
                BenefitTemplateProductORM.product_id == product_id,
            )
            .delete()
        )
        if not deleted:
            raise HTTPException(status_code=404, detail=_("绑定关系不存在"))
    return {"ok": True}


# =============================================================================
#  后台管理 — 模板绑定区域（细粒度操作）
# =============================================================================

@router.post("/api/admin/benefits/templates/{template_id}/regions", summary="绑定区域到模板")
async def admin_bind_regions(
    template_id: int,
    data: dict = Body(..., description='{"regions": ["US", "EU"]}'),
    _token: str = Depends(admin_auth_required),
):
    """追加绑定（不覆盖已有的）"""
    regions = data.get("regions", [])
    if not regions:
        raise HTTPException(status_code=400, detail=_("regions 不能为空"))

    with get_db() as db:
        template = db.query(BenefitTemplateORM).filter(BenefitTemplateORM.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail=_("模板不存在"))

        existing = set(
            r for (r,) in
            db.query(BenefitTemplateRegionORM.region_code)
            .filter(BenefitTemplateRegionORM.template_id == template_id)
            .all()
        )
        added = 0
        for region in regions:
            rc = str(region).upper()
            if rc not in existing:
                db.add(BenefitTemplateRegionORM(template_id=template_id, region_code=rc))
                added += 1

    return {"ok": True, "added": added}


@router.delete("/api/admin/benefits/templates/{template_id}/regions/{region_code}", summary="解绑区域")
async def admin_unbind_region(
    template_id: int,
    region_code: str,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        deleted = (
            db.query(BenefitTemplateRegionORM)
            .filter(
                BenefitTemplateRegionORM.template_id == template_id,
                BenefitTemplateRegionORM.region_code == region_code.upper(),
            )
            .delete()
        )
        if not deleted:
            raise HTTPException(status_code=404, detail=_("绑定关系不存在"))
    return {"ok": True}


# =============================================================================
#  后台管理 — 价格同步
# =============================================================================

@router.post("/api/admin/benefits/sync-prices", summary="同步 Apple 各地区价格（管理后台）")
async def admin_sync_prices(
    data: dict = Body(..., description='{"product_ids": [1, 2, 3]} 或 {"sync_all": true}'),
    _token: str = Depends(admin_auth_required),
):
    """
    从 App Store Connect API 同步商品的各地区价格。

    请求示例：
    - 同步指定商品: {"product_ids": [1, 2, 3]}
    - 同步所有 Apple 商品: {"sync_all": true}

    需要配置环境变量：
    - APP_STORE_KEY_ID
    - APP_STORE_ISSUER_ID
    - APP_STORE_PRIVATE_KEY_PATH 或 APP_STORE_PRIVATE_KEY
    """
    from services.appstore_service import sync_product_prices

    sync_all = data.get("sync_all", False)
    product_ids = data.get("product_ids", [])

    with get_db() as db:
        if sync_all:
            # 获取所有有 apple_product_id 的商品
            products = db.query(BenefitProductORM).filter(
                BenefitProductORM.apple_product_id != "",
                BenefitProductORM.is_active == True,
            ).all()
            product_ids = [p.id for p in products]
        elif not product_ids:
            raise HTTPException(status_code=400, detail=_("请提供 product_ids 或设置 sync_all=true"))

        # 获取商品信息
        products = db.query(BenefitProductORM).filter(
            BenefitProductORM.id.in_(product_ids),
        ).all()

        if not products:
            raise HTTPException(status_code=404, detail=_("未找到商品"))

    # 同步价格
    results = []
    for product in products:
        if not product.apple_product_id:
            results.append({
                "product_id": product.id,
                "title": product.title,
                "ok": False,
                "reason": "apple_product_id 为空",
            })
            continue

        result = sync_product_prices(product.id, product.apple_product_id)
        results.append({
            "product_id": product.id,
            "title": product.title,
            "apple_product_id": product.apple_product_id,
            **result,
        })

    success_count = sum(1 for r in results if r.get("ok"))
    total_synced = sum(r.get("synced", 0) for r in results if r.get("ok"))

    return {
        "ok": True,
        "summary": {
            "total_products": len(results),
            "success": success_count,
            "failed": len(results) - success_count,
            "total_prices_synced": total_synced,
        },
        "details": results,
    }


@router.get("/api/admin/benefits/prices/{product_id}", summary="查看商品各地区价格（管理后台）")
async def admin_get_product_prices(
    product_id: int,
    _token: str = Depends(admin_auth_required),
):
    """查看指定商品的各地区价格列表"""
    with get_db() as db:
        product = db.query(BenefitProductORM).filter(BenefitProductORM.id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=_("商品不存在"))

        prices = db.query(ProductPriceORM).filter(
            ProductPriceORM.product_id == product_id
        ).order_by(ProductPriceORM.territory).all()

        price_list = [
            {
                "territory": p.territory,
                "currency": p.currency,
                "price": p.price,
            }
            for p in prices
        ]

    return {
        "ok": True,
        "product_id": product_id,
        "title": product.title,
        "apple_product_id": product.apple_product_id,
        "prices": price_list,
        "total": len(price_list),
    }


@router.get("/api/admin/benefits/test-apple-price", summary="测试获取 Apple 商品价格（管理后台）")
async def admin_test_apple_price(
    apple_product_id: str = Query(..., description="Apple 产品ID，如 Points4.99"),
    _token: str = Depends(admin_auth_required),
):
    """
    直接通过 Apple 产品ID查询各地区价格（不存储，仅测试）。
    用于验证 App Store Connect API 是否正常工作。
    """
    from services.appstore_service import get_in_app_purchase_prices, get_subscription_prices

    # 先尝试作为 IAP 获取
    prices = get_in_app_purchase_prices(apple_product_id)

    # 如果没有数据，尝试作为订阅获取
    if not prices:
        prices = get_subscription_prices(apple_product_id)

    return {
        "ok": bool(prices),
        "apple_product_id": apple_product_id,
        "prices_count": len(prices),
        "prices": prices[:20] if prices else [],  # 只返回前20个
    }
