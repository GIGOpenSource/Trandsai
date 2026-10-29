"""智能体列表 / 朋友圈推荐瀑布。只在入口矩阵允许的表面调用。"""
from __future__ import annotations

import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.recommend_tags import (
    ALL_SENTINEL,
    apply_to_covers,
    assemble_user_tags_from_orm,
    mapped_tags_for_ua,
    normalize_surface,
    normalize_tag_list,
    object_matches_tags,
    short_lang,
)

logger = logging.getLogger(__name__)

CACHE_VER_KEY = "recommend:cache_ver"

CountFn = Callable[[Optional[Sequence[str]], str, Optional[str]], int]

_local: Dict[str, Any] = {"ver": None, "strategies": None, "tag_defs": None}


@dataclass
class WaterfallResult:
    layer: str  # strategy | language | all
    strategy_id: Optional[int] = None
    agent_tags: Optional[List[str]] = None  # None=不加 tags 条件；[]=智能体侧全部
    agent_match: str = "any"
    lang_eq: Optional[str] = None
    is_global: bool = False
    matched_strategy_id: Optional[int] = None
    matched_strategy_ids: Optional[List[int]] = None  # 所有命中策略 ID（priority 高→低）
    matched_filters: Optional[List[dict]] = None  # [{id, agent_tags, agent_match, priority}]
    fallback_layer: str = "all"
    popularity_fallback: bool = False  # True=兜底按全站亲密度总和降序

    def as_debug(self) -> dict:
        return {
            "matched_strategy_id": self.matched_strategy_id,
            "matched_strategy_ids": self.matched_strategy_ids,
            "fallback_layer": self.fallback_layer,
            "is_global": self.is_global,
            "popularity_fallback": self.popularity_fallback,
        }


def should_apply_waterfall(
    *,
    special: Optional[str] = None,
    is_created: Optional[bool] = None,
    intimacy_value: Optional[float] = None,
    intimacy_gt: bool = False,
    min_turns: int = 0,
) -> bool:
    """仅推荐 / 全部 走策略瀑布。

    - special=recommend → companions_recommend
    - intimacy_value=0 且非 is_created / 非 min_turns / 非 intimacy_gt → companions_all
    - special=message、is_created=true、恋人、亲密度记录、机器人页：不套策略
    """
    spec = (special or "").strip()
    if spec == "recommend":
        return True
    if spec:
        return False
    if is_created is True:
        return False
    if min_turns and int(min_turns) > 0:
        return False
    if intimacy_gt:
        return False
    if intimacy_value is None:
        return False
    try:
        return float(intimacy_value) == 0.0
    except (TypeError, ValueError):
        return False


def bump_recommend_cache() -> None:
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if r:
            r.incr(CACHE_VER_KEY)
    except Exception:
        logger.warning("recommend cache bump redis failed", exc_info=True)
    _local["ver"] = None
    _local["strategies"] = None
    _local["tag_defs"] = None


def _cache_ver() -> str:
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if r:
            return r.get(CACHE_VER_KEY) or "0"
    except Exception:
        pass
    return "local"


def _ensure_loaded() -> None:
    ver = _cache_ver()
    if _local["ver"] == ver and _local["strategies"] is not None and _local["tag_defs"] is not None:
        return
    from core.database import RecommendStrategyORM, RecommendTagDefORM, get_db

    with get_db() as db:
        defs = db.query(RecommendTagDefORM).all()
        tag_defs = {
            (row.key, row.side): {
                "key": row.key,
                "side": row.side,
                "group": row.group,
                "label_zh": row.label_zh or "",
                "label_en": row.label_en or "",
                "enabled": bool(row.enabled),
                "source": row.source or "ops",
            }
            for row in defs
        }
        rows = db.query(RecommendStrategyORM).all()
        strategies = [_strategy_to_dict(s) for s in rows]
    _local["ver"] = ver
    _local["tag_defs"] = tag_defs
    _local["strategies"] = strategies


def _strategy_to_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name or "",
        "enabled": bool(row.enabled),
        "priority": int(row.priority or 0),
        "apply_to": row.apply_to or "both",
        "user_tags": normalize_tag_list(row.user_tags),
        "agent_tags": normalize_tag_list(row.agent_tags),
        "user_match": (row.user_match or "any"),
        "agent_match": (row.agent_match or "any"),
        "created_by": row.created_by or "",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def load_tag_def_map() -> Dict[Tuple[str, str], str]:
    _ensure_loaded()
    return {(k, s): v["group"] for (k, s), v in (_local["tag_defs"] or {}).items()}


def load_tag_defs() -> Dict[Tuple[str, str], dict]:
    _ensure_loaded()
    return dict(_local["tag_defs"] or {})


def load_all_strategies() -> List[dict]:
    _ensure_loaded()
    return list(_local["strategies"] or [])


def load_enabled_strategies(surface: str) -> List[dict]:
    surf = normalize_surface(surface)
    out = [
        s for s in load_all_strategies()
        if s.get("enabled") and apply_to_covers(s.get("apply_to"), surf)
    ]
    out.sort(key=lambda s: (-int(s.get("priority") or 0), int(s.get("id") or 0)))
    return out


def reload_companion_memory_cache() -> None:
    try:
        from core.state import get_companion_manager

        cm = get_companion_manager()
        if cm:
            cm._companions.clear()
            cm._load_all()
    except Exception:
        logger.warning("reload companion cache after recommend save failed", exc_info=True)


def collect_user_tags(db, user_id: Optional[int], ui_lang: str) -> List[str]:
    """未登录空 set。已登录用 users.language 组装 lang:{短码}；未配置语言则不带 lang:*。"""
    if not user_id:
        return []
    from core.database import UserORM

    user = db.query(UserORM).filter(UserORM.id == user_id).first()
    if user:
        try:
            from services.ad_attribution import merge_attribution_tags_for_user

            merge_attribution_tags_for_user(db, user_id)
            db.refresh(user)
        except Exception:
            logger.warning("collect_user_tags merge attribution failed user_id=%s", user_id, exc_info=True)
    mapped = mapped_tags_for_ua(db, getattr(user, "ua_tags", None) or []) if user else []
    return assemble_user_tags_from_orm(user, ui_lang, mapped_tags=mapped)


def resolve_waterfall(
    *,
    surface: str,
    user_tags: Sequence[str],
    ui_lang: str,
    strategies: Sequence[dict],
    count_fn: CountFn,
    logged_in: bool = True,
    tag_def_map: Optional[Dict[Tuple[str, str], str]] = None,
) -> WaterfallResult:
    """priority DESC，同分 id ASC。只按用户条件选策略，智能体池空不改选下一条。

    无性别 / 保密：只能命中未含 gender:* 的广告策略（临时 → 广告全量）。
    有性别：命中性别+广告后锁定，不落到仅 ads 的临时策略。
    杀开关（表空/全关）：全量，层内语种软优先。
    有启用策略但未命中 / 未登录：语言硬筛 → 全量。
    """
    lang = short_lang(ui_lang)
    tag_def_map = tag_def_map if tag_def_map is not None else load_tag_def_map()
    user_set = list(user_tags or [])
    surface = normalize_surface(surface)

    enabled = [
        s for s in strategies
        if s.get("enabled") and apply_to_covers(s.get("apply_to"), surface)
    ]
    enabled.sort(key=lambda s: (-int(s.get("priority") or 0), int(s.get("id") or 0)))

    if logged_in and enabled:
        from services.ad_attribution import split_ad_and_other_user_tags, user_has_ad_audience_tags

        # 已按 priority DESC 排序：用户条件命中即采用。
        # 智能体池是否为空只影响列表人数，不得改选成更宽的临时策略。
        # 广告侧：未勾选广义/精准 → 仅自然量；已勾选 → 精准 ∪ 广义并集。
        for s in enabled:
            user_cond = normalize_tag_list(s.get("user_tags"))
            ad_cond, other_cond = split_ad_and_other_user_tags(user_cond)
            user_tag_set = set(normalize_tag_list(user_set))
            if ad_cond:
                if not (user_tag_set & set(ad_cond)):
                    continue
            elif user_has_ad_audience_tags(user_set):
                continue
            if other_cond and not object_matches_tags(
                user_set,
                other_cond,
                "user",
                tag_def_map,
                s.get("user_match") or "any",
            ):
                continue
            agent_tags = normalize_tag_list(s.get("agent_tags"))
            agent_match = s.get("agent_match") or "any"
            sid = s.get("id")
            return WaterfallResult(
                layer="strategy",
                strategy_id=sid,
                agent_tags=agent_tags if agent_tags else None,
                agent_match=agent_match,
                is_global=not user_cond,
                matched_strategy_id=sid,
                matched_strategy_ids=[sid],
                matched_filters=[{
                    "id": sid,
                    "priority": int(s.get("priority") or 0),
                    "agent_tags": list(agent_tags),
                    "agent_match": agent_match,
                }],
                fallback_layer="strategy",
            )

        # 推荐场景：启用策略都未命中 → 不兜底
        if surface == "companions_recommend":
            return WaterfallResult(
                layer="empty",
                matched_strategy_id=None,
                fallback_layer="empty",
                popularity_fallback=False,
            )

    # 本场景无启用策略：全量，兜底按受欢迎度排序
    if logged_in and not enabled:
        return WaterfallResult(
            layer="all",
            matched_strategy_id=None,
            fallback_layer="all",
            popularity_fallback=True,
        )

    if lang and count_fn(None, "any", lang) > 0:
        return WaterfallResult(
            layer="language",
            lang_eq=lang,
            matched_strategy_id=None,
            fallback_layer="language",
            popularity_fallback=True,
        )
    return WaterfallResult(
        layer="all",
        matched_strategy_id=None,
        fallback_layer="all",
        popularity_fallback=True,
    )


def agent_tag_match_clause(tags_column, agent_tags: Optional[Sequence[str]], tag_def_map, match_mode: str = "any"):
    """智能体 tags 包含策略列出的每一个 agent_tag（集合包含，顺序无关）。空标签不限制。"""
    from sqlalchemy.dialects.postgresql import array as pg_array

    _ = (tag_def_map, match_mode)
    keys = normalize_tag_list(agent_tags)
    if not keys:
        return None
    if len(keys) == 1:
        return tags_column.op("?")(keys[0])
    return tags_column.op("?&")(pg_array(keys))


def apply_agent_tag_filters(query, tags_column, agent_tags: Optional[Sequence[str]], tag_def_map, match_mode: str = "any"):
    """智能体 tags ⊇ agent_tags。空/None 不加 tags 条件。"""
    clause = agent_tag_match_clause(tags_column, agent_tags, tag_def_map, match_mode)
    if clause is None:
        return query
    return query.filter(clause)


def apply_waterfall_to_companion_query(query, pool: WaterfallResult, tag_def_map, CompanionORM):
    from sqlalchemy import false

    if pool.layer == "empty":
        return query.filter(false())
    if pool.layer == "strategy":
        from sqlalchemy import or_

        filters = pool.matched_filters or []
        if filters:
            clauses = []
            for item in filters:
                clause = agent_tag_match_clause(
                    CompanionORM.tags,
                    item.get("agent_tags"),
                    tag_def_map,
                    item.get("agent_match") or "any",
                )
                if clause is None:
                    return query
                clauses.append(clause)
            if len(clauses) == 1:
                return query.filter(clauses[0])
            return query.filter(or_(*clauses))
        return apply_agent_tag_filters(
            query, CompanionORM.tags, pool.agent_tags, tag_def_map, pool.agent_match
        )
    if pool.layer == "language" and pool.lang_eq:
        return query.filter(CompanionORM.language == pool.lang_eq)
    return query


def strategy_rank_order_clause(tags_column, pool: WaterfallResult, tag_def_map):
    """命中策略按 priority 排序：越靠前的策略其智能体越靠前。未命中策略层则返回 None。"""
    from sqlalchemy import case

    if not pool or pool.layer != "strategy":
        return None
    filters = pool.matched_filters or []
    if not filters:
        return None
    whens = []
    for idx, item in enumerate(filters):
        clause = agent_tag_match_clause(
            tags_column,
            item.get("agent_tags"),
            tag_def_map,
            item.get("agent_match") or "any",
        )
        if clause is None:
            continue
        whens.append((clause, idx))
    if not whens:
        return None
    return case(*whens, else_=len(filters))


def device_lang_filter(filter_lang: str = "", ui_lang: str = "") -> str:
    """归一成 companions.language 短码。前者优先，空则用后者。"""
    from core.i18n import normalize_ui_language

    raw = (filter_lang or "").strip() or (ui_lang or "").strip()
    if not raw:
        return ""
    return normalize_ui_language(short_lang(raw) or raw)


def resolve_companion_list_lang(
    db,
    user_id: Optional[int],
    *,
    filter_lang: str = "",
    accept_lang: str = "",
) -> str:
    """列表语种硬筛，与推荐策略无关。

    优先级：显式 filter_lang > users.language > Accept-Language。
    用户未设置语言时不丢匹配，回退请求头。
    """
    explicit = device_lang_filter(filter_lang, "")
    if explicit:
        return explicit
    profile = ""
    if db is not None and user_id:
        try:
            from core.database import UserORM

            u = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
            profile = (getattr(u, "language", None) or "") if u else ""
        except Exception:
            profile = ""
    return device_lang_filter(profile, accept_lang)


def apply_hard_filters(query, CompanionORM, filter_lang: str = "", gender: str = "", orientation: str = ""):
    from core.i18n import normalize_ui_language

    fl = short_lang(filter_lang)
    if fl:
        fl = normalize_ui_language(fl)
        query = query.filter(CompanionORM.language == fl)
    g = (gender or "").strip()
    if g:
        try:
            from core.gender import stored_gender
            g = stored_gender(g, default="")
        except Exception:
            pass
        if g in ("男", "女"):
            query = query.filter(CompanionORM.gender == g)
    ori = (orientation or "").strip()
    if ori:
        query = query.filter(CompanionORM.sexual_orientation == ori)
    return query


RECOMMEND_POOL_PAD_MAX = 5  # 策略池空，或 ≤5 / 不够一页时热门补全


def should_fill_moments_lang_tiers(*, strategy_matched: bool) -> bool:
    """朋友圈：命中推荐策略后不再同语言/其他语言补全，避免把未匹配 agent_tags 的动态混进 feed。"""
    return not bool(strategy_matched)


def should_pad_recommend_pool(
    strategy_total: int,
    page_size: int,
    *,
    strategy_matched: bool = False,
) -> bool:
    """仅「未命中策略」时热门补全。命中后只推策略池，不够也不拿热门顶，避免智能体规则被冲掉。"""
    if strategy_matched:
        return False
    n = max(int(strategy_total or 0), 0)
    ps = max(int(page_size or 20), 1)
    return n <= RECOMMEND_POOL_PAD_MAX or n < ps


def window_for_strategy_then_fill(
    strategy_total: int,
    fill_total: int,
    offset: int,
    limit: int,
) -> Tuple[int, int, int, int, int]:
    """合并「策略结果 + 热门补全」后的分页窗口。

    返回 (strategy_offset, strategy_limit, fill_offset, fill_limit, combined_total)。
    """
    s_n = max(int(strategy_total or 0), 0)
    f_n = max(int(fill_total or 0), 0)
    off = max(int(offset or 0), 0)
    lim = max(int(limit or 20), 1)
    combined = s_n + f_n
    if off >= s_n:
        return 0, 0, off - s_n, lim, combined
    take_s = min(lim, s_n - off)
    return off, take_s, 0, lim - take_s, combined


def window_for_three_tiers(
    n1: int,
    n2: int,
    n3: int,
    offset: int,
    limit: int,
) -> Tuple[int, int, int, int, int, int, int]:
    """三段瀑布分页：主池 → 第二段 → 第三段。

    返回 (o1, l1, o2, l2, o3, l3, combined_total)。
    """
    a_off, a_lim, rest_off, rest_lim, _ = window_for_strategy_then_fill(
        n1, n2 + n3, offset, limit
    )
    b_off, b_lim, c_off, c_lim, _ = window_for_strategy_then_fill(
        n2, n3, rest_off, rest_lim
    )
    combined = max(int(n1 or 0), 0) + max(int(n2 or 0), 0) + max(int(n3 or 0), 0)
    return a_off, a_lim, b_off, b_lim, c_off, c_lim, combined


def interleave_companion_rounds(
    *tiers: Sequence[Tuple[Any, Any]],
) -> List[Any]:
    """单段内按智能体轮转：每轮每人 1 条，人的先后取该段原排序。

    输入为已按原排序的 (moment_id, companion_id)。
    多段请各自调用后再拼接（推荐出完 → 同语言 → 其他语言）。
    """
    queues: List[OrderedDict] = []
    for rows in tiers:
        q: "OrderedDict[Any, deque]" = OrderedDict()
        for item in rows or ():
            if not item:
                continue
            mid, cid = item[0], item[1]
            if mid is None:
                continue
            key = cid or ""
            if key not in q:
                q[key] = deque()
            q[key].append(mid)
        queues.append(q)

    out: List[Any] = []
    while any(queues):
        progressed = False
        for q in queues:
            for cid in list(q.keys()):
                bucket = q.get(cid)
                if not bucket:
                    q.pop(cid, None)
                    continue
                out.append(bucket.popleft())
                progressed = True
                if not bucket:
                    q.pop(cid, None)
        if not progressed:
            break
    return out


def resolve_for_request(
    db,
    *,
    surface: str,
    user_id: Optional[int],
    ui_lang: str,
    count_fn: CountFn,
    logged_in: Optional[bool] = None,
) -> WaterfallResult:
    if logged_in is None:
        logged_in = bool(user_id)
    user_tags = collect_user_tags(db, user_id if logged_in else None, ui_lang)
    return resolve_waterfall(
        surface=surface,
        user_tags=user_tags,
        ui_lang=ui_lang,
        strategies=load_enabled_strategies(surface) if logged_in else [],
        count_fn=count_fn,
        logged_in=logged_in,
        tag_def_map=load_tag_def_map(),
    )


def validate_strategy_tags(user_tags: Sequence[str], agent_tags: Sequence[str], tag_defs: Optional[dict] = None) -> Optional[str]:
    """返回错误信息；双侧空拒绝。每个 key 必须在对应 side 字典里。

    广义/精准广告可空 []：保存允许；运行时仅匹配无广告标识用户。
    勾选广义+精准时按并集 OR 匹配。
    """
    u = normalize_tag_list(user_tags)
    a = normalize_tag_list(agent_tags)
    if not u and not a:
        return "both_sides_empty"
    defs = tag_defs if tag_defs is not None else load_tag_defs()
    for key in u:
        if (key, "user") not in defs:
            if key.startswith("ua:") or key.startswith("audience:"):
                continue
            return f"unknown_user_tag:{key}"
    for key in a:
        if (key, "agent") not in defs:
            return f"unknown_agent_tag:{key}"
    return None


def flatten_grouped_selection(group_values: Dict[str, Sequence[str]]) -> List[str]:
    """组选全部或未选 → 不写入该组；所有组全部 → []。"""
    out: List[str] = []
    for _g, vals in (group_values or {}).items():
        items = [str(v).strip() for v in (vals or []) if str(v).strip()]
        if not items or ALL_SENTINEL in items or "__all__" in items:
            continue
        for v in items:
            if v not in out:
                out.append(v)
    return out
