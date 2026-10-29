"""用户增长 / 归因写入（独立链路，失败不挡登录与支付）。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote

from sqlalchemy.orm import Session

from core.app_key import DEFAULT_APP_KEY, app_key_from_headers, normalize_app_key
from core.client_context import extract_client_context
from core.database import UserAttributionEventORM
from core.log_util import pipeline_log

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

_INSTALL_EVENTS = frozenset({"install", "first_open"})
_AD_INSTALL_SOURCES = frozenset({"ads", "paid", "ad", "advertising"})


def _clean(value: Any, max_len: int = 128) -> str:
    return str(value or "").strip()[:max_len]


def _pick(*values: Any) -> str:
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def _as_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _normalize_companion_id(value: Any) -> str:
    """ companion_id 可选：缺失/空/null/异常类型 → 空字符串，不抛错。"""
    if value is None:
        return ""
    try:
        if isinstance(value, bool):
            return ""
        s = str(value).strip()
        return s[:8] if s else ""
    except Exception:
        return ""


def extract_event_companion_id(
    body: Optional[Dict[str, Any]] = None,
    *,
    firebase_event_params: Optional[Any] = None,
    raw_payload: Optional[Any] = None,
) -> str:
    """从产品埋点请求中解析 companion_id（顶层 / firebase / raw）；未传则返回 ''。"""
    try:
        b = body if isinstance(body, dict) else {}
        fb = _as_dict(firebase_event_params)
        raw = _as_dict(raw_payload)
        for source in (
            b.get("companion_id"),
            b.get("companionId"),
            fb.get("companion_id"),
            fb.get("companionId"),
            raw.get("companion_id"),
            raw.get("companionId"),
        ):
            cid = _normalize_companion_id(source)
            if cid:
                return cid
        return ""
    except Exception:
        logger.debug("extract_event_companion_id failed", exc_info=True)
        return ""


def _parse_moment_id(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def extract_event_moment_id(
    body: Optional[Dict[str, Any]] = None,
    *,
    firebase_event_params: Optional[Any] = None,
    raw_payload: Optional[Any] = None,
) -> Optional[int]:
    """解析 moment_id（顶层 / firebase / raw 的 moment_id、momentId、target_id）。"""
    try:
        b = body if isinstance(body, dict) else {}
        fb = _as_dict(firebase_event_params)
        raw = _as_dict(raw_payload)
        for source in (
            b.get("moment_id"),
            b.get("momentId"),
            fb.get("moment_id"),
            fb.get("momentId"),
            fb.get("target_id"),
            fb.get("targetId"),
            raw.get("moment_id"),
            raw.get("momentId"),
            raw.get("target_id"),
            raw.get("targetId"),
        ):
            mid = _parse_moment_id(source)
            if mid:
                return mid
        return None
    except Exception:
        logger.debug("extract_event_moment_id failed", exc_info=True)
        return None


def resolve_user_id_by_device(db: Session, device_id: str) -> Optional[int]:
    """同 device 最近一条已绑定用户的归因，用于登录前 product 埋点补 user_id。"""
    device_id = (device_id or "").strip()
    if not device_id:
        return None
    row = (
        db.query(UserAttributionEventORM.user_id)
        .filter(
            UserAttributionEventORM.device_id == device_id,
            UserAttributionEventORM.user_id.isnot(None),
        )
        .order_by(UserAttributionEventORM.id.desc())
        .first()
    )
    if not row or not row[0]:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def extract_gclid_and_campaign(
    *,
    gclid: str = "",
    gad_campaignid: str = "",
    raw_payload: Optional[Any] = None,
    firebase_event_params: Optional[Any] = None,
) -> Tuple[str, str]:
    """从顶层 / raw_payload / firebase_event_params / play_referrer 抽出 gclid、gad_campaignid。"""
    payload = _as_dict(raw_payload)
    fb = _as_dict(firebase_event_params)
    out_gclid = (
        _clean(gclid, 256)
        or _clean(payload.get("gclid"), 256)
        or _clean(fb.get("gclid"), 256)
    )
    out_camp = (
        _clean(gad_campaignid, 128)
        or _clean(payload.get("gad_campaignid"), 128)
        or _clean(payload.get("gad_campaign_id"), 128)
        or _clean(payload.get("campaignid"), 128)
        or _clean(fb.get("gad_campaignid"), 128)
        or _clean(fb.get("gad_campaign_id"), 128)
        or _clean(fb.get("campaignid"), 128)
    )

    referrer = payload.get("play_referrer") or payload.get("referrer") or ""
    if isinstance(referrer, str) and referrer.strip():
        ref = unquote(referrer.strip())
        if "?" in ref:
            ref = ref.split("?", 1)[1]
        params = parse_qs(ref, keep_blank_values=False)
        if not out_gclid:
            vals = params.get("gclid") or []
            if vals:
                out_gclid = _clean(vals[0], 256)
        if not out_camp:
            for key in ("gad_campaignid", "gad_campaign_id", "campaignid", "utm_campaign"):
                vals = params.get(key) or []
                if vals:
                    out_camp = _clean(vals[0], 128)
                    break
    return out_gclid, out_camp


def _normalize_firebase_params(raw: Any) -> dict:
    """firebase_event_params：尽量保持 string 值（与客户端约定一致）。"""
    src = _as_dict(raw)
    out: Dict[str, Any] = {}
    for k, v in src.items():
        key = str(k or "").strip()
        if not key:
            continue
        if v is None:
            continue
        if isinstance(v, bool):
            out[key] = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            try:
                out[key] = json.dumps(v, ensure_ascii=False, default=str)
            except Exception:
                out[key] = str(v)
        else:
            out[key] = str(v)
    return out


def _pick_utm(body: dict, raw_payload: dict, fb_params: dict, key: str) -> str:
    return _clean(
        _pick(body.get(key), raw_payload.get(key), fb_params.get(key)),
        128,
    )


def build_attribution_fields(
    request: "Request",
    payload: Dict[str, Any],
    *,
    x_device_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """合并 body 与 header；body 优先，header 补空。抽出 gclid / gad_campaignid 到独立字段。"""
    ctx = extract_client_context(request, x_device_id=x_device_id)
    body = payload or {}

    device_id = _pick(body.get("device_id"), ctx.get("device_id"))
    ip = _pick(body.get("ip"), ctx.get("ip"))
    ip_country = _pick(body.get("ip_country"), ctx.get("ip_country"), ctx.get("storefront_country"))
    event_type = _clean(body.get("event_type") or "install", 32) or "install"

    referrer_user_id = body.get("referrer_user_id")
    try:
        referrer_user_id = int(referrer_user_id) if referrer_user_id not in (None, "") else None
    except (TypeError, ValueError):
        referrer_user_id = None

    push_enabled = body.get("push_enabled")
    if push_enabled is not None and not isinstance(push_enabled, bool):
        push_enabled = str(push_enabled).strip().lower() in ("1", "true", "yes")

    raw_payload = _as_dict(body.get("raw_payload"))
    firebase_event_params = _normalize_firebase_params(body.get("firebase_event_params"))
    gclid, gad_campaignid = extract_gclid_and_campaign(
        gclid=_clean(body.get("gclid"), 256),
        gad_campaignid=_clean(body.get("gad_campaignid"), 128),
        raw_payload=raw_payload,
        firebase_event_params=firebase_event_params,
    )
    if gclid and not raw_payload.get("gclid"):
        raw_payload = {**raw_payload, "gclid": gclid}

    product_event = _clean(body.get("product_event"), 64)
    firebase_event_name = _clean(body.get("firebase_event_name"), 128)
    if event_type == "product":
        if not product_event and firebase_event_name:
            product_event = firebase_event_name[:64]
        if not firebase_event_name and product_event:
            firebase_event_name = product_event

    # 马甲：body.app_key > X-App-Key > 默认 trandsai（nginx 马甲域名会注入 header）
    app_key = normalize_app_key(
        _pick(body.get("app_key"), app_key_from_headers(request.headers)),
        default=DEFAULT_APP_KEY,
    )

    return {
        "user_id": user_id,
        "device_id": device_id,
        "event_type": event_type,
        "app_key": app_key,
        "install_source": _clean(body.get("install_source"), 32),
        "utm_source": _pick_utm(body, raw_payload, firebase_event_params, "utm_source"),
        "utm_medium": _pick_utm(body, raw_payload, firebase_event_params, "utm_medium"),
        "utm_campaign": _pick_utm(body, raw_payload, firebase_event_params, "utm_campaign"),
        "utm_content": _pick_utm(body, raw_payload, firebase_event_params, "utm_content"),
        "utm_term": _pick_utm(body, raw_payload, firebase_event_params, "utm_term"),
        "gclid": gclid,
        "gad_campaignid": gad_campaignid,
        "invite_code": _clean(body.get("invite_code"), 64),
        "referrer_user_id": referrer_user_id,
        "ad_id": _clean(body.get("ad_id"), 128),
        "ad_id_type": _clean(body.get("ad_id_type"), 16),
        "idfv": _clean(body.get("idfv"), 64),
        "android_id": _clean(body.get("android_id"), 64),
        "network": _clean(body.get("network"), 16),
        "push_enabled": push_enabled,
        "login_method": _clean(body.get("login_method"), 20),
        "app_version": _pick(body.get("app_version"), ctx.get("app_version")),
        "os": _pick(body.get("os"), ctx.get("os")),
        "os_version": _pick(body.get("os_version"), ctx.get("os_version")),
        "device_model": _pick(body.get("device_model"), ctx.get("device_model")),
        "locale": _pick(body.get("locale"), body.get("language"), ctx.get("locale")),
        "timezone": _pick(body.get("timezone"), ctx.get("timezone")),
        "storefront_country": _pick(body.get("storefront_country"), ctx.get("storefront_country")),
        "ip": ip[:64],
        "ip_country": ip_country[:8],
        "first_ip": ip[:64] if event_type in _INSTALL_EVENTS else "",
        "first_country": ip_country[:8] if event_type in _INSTALL_EVENTS else "",
        "attribution_provider": _clean(body.get("attribution_provider"), 32),
        "app_env": _clean(body.get("app_env"), 16),
        "product_event": product_event,
        "firebase_event_name": firebase_event_name,
        "firebase_event_params": firebase_event_params,
        "raw_payload": raw_payload,
        "companion_id": extract_event_companion_id(
            body,
            firebase_event_params=firebase_event_params,
            raw_payload=raw_payload,
        ),
        "moment_id": extract_event_moment_id(
            body,
            firebase_event_params=firebase_event_params,
            raw_payload=raw_payload,
        ),
    }


def link_device_attribution_to_user(db: Session, device_id: str, user_id: int) -> int:
    """注册/登录后，把同 device_id 的历史匿名事件补上 user_id。"""
    device_id = (device_id or "").strip()
    if not device_id or not user_id:
        return 0
    rows = (
        db.query(UserAttributionEventORM)
        .filter(
            UserAttributionEventORM.device_id == device_id,
            UserAttributionEventORM.user_id.is_(None),
        )
        .all()
    )
    for row in rows:
        row.user_id = user_id
    if rows:
        db.flush()
    return len(rows)


def _enrich_existing_install_row(row: UserAttributionEventORM, fields: Dict[str, Any]) -> bool:
    """用更新的上报补全已有 install 行的 gclid / gad_campaignid / raw / install_source。"""
    dirty = False
    raw = _as_dict(fields.get("raw_payload"))
    existing_raw = _as_dict(row.raw_payload)
    merged_raw = {**existing_raw, **raw} if (existing_raw or raw) else existing_raw

    gclid, camp = extract_gclid_and_campaign(
        gclid=_pick(fields.get("gclid"), row.gclid),
        gad_campaignid=_pick(fields.get("gad_campaignid"), getattr(row, "gad_campaignid", None)),
        raw_payload=merged_raw,
        firebase_event_params=fields.get("firebase_event_params"),
    )
    if gclid and gclid != (row.gclid or ""):
        row.gclid = gclid[:256]
        dirty = True
    if camp and camp != (getattr(row, "gad_campaignid", None) or ""):
        row.gad_campaignid = camp[:128]
        dirty = True

    src = _clean(fields.get("install_source"), 32)
    cur_src = (row.install_source or "").strip().lower()
    if src:
        if not cur_src or cur_src == "unknown":
            row.install_source = src
            dirty = True
        elif src.lower() in _AD_INSTALL_SOURCES and cur_src not in _AD_INSTALL_SOURCES:
            row.install_source = src
            dirty = True

    if merged_raw and merged_raw != existing_raw:
        row.raw_payload = merged_raw
        dirty = True
    return dirty


def _find_install_row(db: Session, device_id: str) -> Optional[UserAttributionEventORM]:
    return (
        db.query(UserAttributionEventORM)
        .filter(
            UserAttributionEventORM.device_id == device_id,
            UserAttributionEventORM.event_type.in_(tuple(_INSTALL_EVENTS)),
        )
        .order_by(UserAttributionEventORM.id.asc())
        .first()
    )


def record_attribution_event(
    db: Session,
    fields: Dict[str, Any],
    *,
    allow_duplicate_install: bool = False,
) -> Optional[UserAttributionEventORM]:
    """写入归因事件；install/first_open 默认每 device 只记一次。

    重复 install、或 login/register 携带 gclid 时，都会补全同设备 install 行并打标。
    """
    device_id = (fields.get("device_id") or "").strip()
    if not device_id:
        return None

    event_type = (fields.get("event_type") or "install").strip().lower()
    if event_type in _INSTALL_EVENTS and not allow_duplicate_install:
        row = _find_install_row(db, device_id)
        if row:
            if fields.get("user_id"):
                link_device_attribution_to_user(db, device_id, int(fields["user_id"]))
                row.user_id = int(fields["user_id"])
            _enrich_existing_install_row(row, fields)
            db.flush()
            uid = fields.get("user_id") or row.user_id
            if uid:
                try:
                    from services.ad_attribution import merge_attribution_tags_for_user

                    merge_attribution_tags_for_user(db, int(uid), device_id=device_id)
                except Exception:
                    logger.warning(
                        "merge attribution tags after enrich install failed user_id=%s",
                        uid,
                        exc_info=True,
                    )
            return None

    row = UserAttributionEventORM(
        user_id=fields.get("user_id"),
        device_id=device_id,
        event_type=event_type[:32],
        install_source=(fields.get("install_source") or "")[:32],
        utm_source=(fields.get("utm_source") or "")[:128],
        utm_medium=(fields.get("utm_medium") or "")[:128],
        utm_campaign=(fields.get("utm_campaign") or "")[:128],
        utm_content=(fields.get("utm_content") or "")[:128],
        utm_term=(fields.get("utm_term") or "")[:128],
        gclid=(fields.get("gclid") or "")[:256],
        gad_campaignid=(fields.get("gad_campaignid") or "")[:128],
        invite_code=(fields.get("invite_code") or "")[:64],
        referrer_user_id=fields.get("referrer_user_id"),
        ad_id=(fields.get("ad_id") or "")[:128],
        ad_id_type=(fields.get("ad_id_type") or "")[:16],
        idfv=(fields.get("idfv") or "")[:64],
        android_id=(fields.get("android_id") or "")[:64],
        network=(fields.get("network") or "")[:16],
        push_enabled=fields.get("push_enabled"),
        login_method=(fields.get("login_method") or "")[:20],
        app_version=(fields.get("app_version") or "")[:32],
        os=(fields.get("os") or "")[:16],
        os_version=(fields.get("os_version") or "")[:32],
        device_model=(fields.get("device_model") or "")[:64],
        locale=(fields.get("locale") or "")[:16],
        timezone=(fields.get("timezone") or "")[:64],
        storefront_country=(fields.get("storefront_country") or "")[:8],
        ip=(fields.get("ip") or "")[:64],
        ip_country=(fields.get("ip_country") or "")[:8],
        first_ip=(fields.get("first_ip") or "")[:64],
        first_country=(fields.get("first_country") or "")[:8],
        attribution_provider=(fields.get("attribution_provider") or "")[:32],
        app_env=(fields.get("app_env") or "")[:16],
        app_key=normalize_app_key(fields.get("app_key"), default=DEFAULT_APP_KEY)[:32],
        product_event=(fields.get("product_event") or "")[:64],
        firebase_event_name=(fields.get("firebase_event_name") or "")[:128],
        companion_id=_clean(fields.get("companion_id"), 8),
        firebase_event_params=fields.get("firebase_event_params") or {},
        raw_payload=fields.get("raw_payload") or {},
    )
    db.add(row)
    db.flush()

    # login/register/purchase：把 gclid / campaign / ads 回填到同设备 install，再打标
    # product 高频埋点：仅在有归因信号时轻量回填 install，不反复打标签
    if event_type == "product":
        has_attr = bool(
            fields.get("gclid")
            or fields.get("gad_campaignid")
            or fields.get("utm_campaign")
            or fields.get("utm_source")
        )
        if has_attr:
            install_row = _find_install_row(db, device_id)
            if install_row:
                if fields.get("user_id") and not install_row.user_id:
                    install_row.user_id = int(fields["user_id"])
                _enrich_existing_install_row(install_row, fields)
                db.flush()
    elif event_type not in _INSTALL_EVENTS:
        install_row = _find_install_row(db, device_id)
        if install_row:
            if fields.get("user_id") and not install_row.user_id:
                install_row.user_id = int(fields["user_id"])
            _enrich_existing_install_row(install_row, fields)
            db.flush()

    uid = fields.get("user_id")
    if uid and event_type != "product":
        try:
            from services.ad_attribution import merge_attribution_tags_for_user

            merge_attribution_tags_for_user(db, int(uid), device_id=device_id)
        except Exception:
            logger.warning("merge attribution tags after record failed user_id=%s", uid, exc_info=True)

    # 产品埋点 → 消息策略引擎（失败不影响归因落库）
    if event_type == "product":
        product_event = str(fields.get("product_event") or "").strip()
        dispatch_uid = uid
        if not dispatch_uid and device_id:
            dispatch_uid = resolve_user_id_by_device(db, device_id)
            if dispatch_uid:
                pipeline_log(
                    "[growth.attribution] msg_strategy user resolved from device device_id=%s user_id=%s product_event=%s",
                    device_id,
                    dispatch_uid,
                    product_event,
                )
        if dispatch_uid and product_event:
            try:
                from services.msg_strategy_engine import try_dispatch_from_attribution

                try_dispatch_from_attribution(
                    int(dispatch_uid),
                    product_event,
                    locale=str(fields.get("locale") or "").strip(),
                    app_env=str(fields.get("app_env") or "").strip(),
                    device_id=device_id,
                    companion_id=str(fields.get("companion_id") or "").strip(),
                    moment_id=fields.get("moment_id"),
                )
            except Exception:
                logger.warning(
                    "[growth.attribution] msg_strategy dispatch hook failed user_id=%s product_event=%s",
                    dispatch_uid,
                    product_event,
                    exc_info=True,
                )
        else:
            pipeline_log(
                "[growth.attribution] msg_strategy hook skip device_id=%s product_event=%s companion_id=%s reason=%s",
                device_id or "",
                product_event,
                fields.get("companion_id") or "",
                "no_user_id_need_x_token_or_device_link" if not dispatch_uid else "no_product_event",
            )
    return row


def touch_attribution_from_request(
    request: "Request",
    user_id: int,
    *,
    event_type: str = "login",
    login_method: str = "",
    x_device_id: Optional[str] = None,
) -> None:
    """登录/注册后静默补一条归因（不抛异常）。"""
    try:
        from core.database import get_db

        fields = build_attribution_fields(
            request,
            {"event_type": event_type, "login_method": login_method},
            x_device_id=x_device_id,
            user_id=user_id,
        )
        if not fields.get("device_id"):
            return
        with get_db() as db:
            link_device_attribution_to_user(db, fields["device_id"], user_id)
            record_attribution_event(db, fields)
            try:
                from services.ad_attribution import merge_attribution_tags_for_user

                merge_attribution_tags_for_user(db, user_id, device_id=fields["device_id"])
            except Exception:
                logger.warning("merge attribution tags on touch failed user_id=%s", user_id, exc_info=True)
            db.commit()
    except Exception:
        logger.warning("touch_attribution_from_request failed user_id=%s", user_id, exc_info=True)
