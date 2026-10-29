import os
import shutil
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
import threading

from sqlalchemy import case, desc, func, nulls_last

from services.affection_math import catch_up_stalled_affection
from core.database import (
    CompanionORM,
    CompanionStateORM,
    UserCompanionStateORM,
    CompanionAgentConfigORM,
    FactORM,
    MomentORM,
    MomentCommentORM,
    MomentLikeORM,
    OutreachJobORM,
    RelationSummaryORM,
    ShortTermMessageORM,
    SystemNotificationORM,
    get_db, UserORM,
)
from services.image_generation import generate_avatar_prompt, generate_image
from services.memory import CompanionMemory
from services.culture_data import infer_language_from_city
from core.gender import stored_gender

logger = logging.getLogger(__name__)


def _filter_last_msgs_by_cleared(
    result: Dict[str, dict],
    companion_ids: List[str],
    user_id: Optional[int],
) -> Dict[str, dict]:
    """丢弃清空墓碑及之前的 last_message（含 flush 回写的旧行）。"""
    if user_id is None or not result:
        return result
    try:
        from core.chat_cache import get_rooms_cleared_at, message_at_or_before_clear
    except Exception:
        return result
    cleared_map = get_rooms_cleared_at(list(companion_ids), int(user_id))
    if not cleared_map:
        return result
    out: Dict[str, dict] = {}
    for cid, last in result.items():
        cleared = cleared_map.get(cid)
        if cleared is None:
            out[cid] = last
            continue
        if not message_at_or_before_clear(last.get("timestamp"), cleared):
            out[cid] = last
    return out


def _lang_prefer_clause(ui_lang: str, match: str):
    """同语种排前面（软优先）。match!=prefer 时返回 None。"""
    if (match or "prefer") != "prefer":
        return None
    lang = (ui_lang or "").strip()
    if not lang:
        return None
    return case((CompanionORM.language == lang, 0), else_=1)


def _normalize_gender(value) -> str:
    """统一性别为 男/女；兼容多语言展示文案。"""
    from core.gender import stored_gender
    return stored_gender(value)


def _counters_from_user_row(row) -> dict:
    """当前用户对某智能体的亲密度/轮数。无记录视为 0，不回退全局 companion_states。"""
    if row is None:
        return {"affection": 0.0, "turns": 0}
    try:
        aff = float(row.affection) if row.affection is not None else 0.0
    except (TypeError, ValueError):
        aff = 0.0
    try:
        turns = int(row.turns) if row.turns is not None else 0
    except (TypeError, ValueError):
        turns = 0
    return {"affection": aff, "turns": turns}


def _batch_user_companion_states(user_id: int, companion_ids: List[str]) -> Dict[str, dict]:
    """Batch load per-user affection/turns for list API (S-10)."""
    if not companion_ids:
        return {}
    with get_db() as db:
        return _batch_user_companion_states_with_db(db, user_id, companion_ids)


def _normalize_message_list_sort(sort: Optional[str]) -> str:
    """消息场景排序：默认 last_message_time；sort=affection 按亲密度倒序。"""
    if (sort or "").strip().lower() == "affection":
        return "affection"
    return "last_message_time"


def _batch_last_messages(
    companion_ids: List[str],
    user_id: Optional[int] = None,
    lang: str = "zh",
) -> Dict[str, dict]:
    """一次查询获取多个 companion 的最后一条消息（有 user_id 时按用户隔离）。
    优先合并 Redis 热房间最新消息，确保 outreach 未 flush 时消息列表也能看到。
    role=moment（朋友圈详情 JSON）不直接展示原文，改为多语言固定文案。
    """
    from core.i18n import _LAST_MESSAGE_FROM_MOMENT, normalize_ui_language

    lang = normalize_ui_language(lang)
    moment_preview = _LAST_MESSAGE_FROM_MOMENT.get(lang, _LAST_MESSAGE_FROM_MOMENT["zh"])

    def _display_content(role: Optional[str], content: str) -> str:
        if (role or "") == "moment":
            return moment_preview
        return content or ""

    if not companion_ids:
        return {}
    with get_db() as db:
        q = db.query(
            ShortTermMessageORM.companion_id,
            func.max(ShortTermMessageORM.id).label("max_id"),
        ).filter(ShortTermMessageORM.companion_id.in_(companion_ids))
        if user_id is not None:
            q = q.filter(ShortTermMessageORM.user_id == user_id)
        subq = q.group_by(ShortTermMessageORM.companion_id).subquery()
        rows = (
            db.query(ShortTermMessageORM)
            .join(subq, ShortTermMessageORM.id == subq.c.max_id)
            .all()
        )
        result = {}
        for m in rows:
            ts = m.created_at
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            result[m.companion_id] = {
                "content": _display_content(m.role, m.content or ""),
                "timestamp": ts.isoformat() if ts else datetime.now(timezone.utc).isoformat(),
            }

    # Redis 热房间可能比 PG 更新（flush 延迟）
    if user_id is not None:
        try:
            from core.chat_cache import bridge_enabled, get_recent

            if bridge_enabled():
                for cid in companion_ids:
                    try:
                        recent = get_recent(cid, n=1, user_id=user_id) or []
                        if not recent:
                            continue
                        last = recent[-1]
                        content = _display_content(last.get("role"), (last.get("content") or "").strip())
                        ts = last.get("timestamp") or ""
                        if not content:
                            continue
                        prev = result.get(cid)
                        if not prev or (ts and ts > (prev.get("timestamp") or "")):
                            result[cid] = {"content": content, "timestamp": ts}
                    except Exception:
                        continue
        except Exception:
            pass
    return _filter_last_msgs_by_cleared(result, companion_ids, user_id)


def _batch_user_companion_states_with_db(db, user_id: int, companion_ids: List[str]) -> Dict[str, dict]:
    """与 _batch_user_companion_states 相同逻辑，但复用已有 db session。"""
    if not companion_ids:
        return {}
    user_rows = (
        db.query(UserCompanionStateORM)
        .filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id.in_(companion_ids),
        )
        .all()
    )
    return {r.companion_id: _counters_from_user_row(r) for r in user_rows}


def _batch_last_messages_with_db(
    db,
    companion_ids: List[str],
    user_id: Optional[int] = None,
    lang: str = "zh",
) -> Dict[str, dict]:
    """与 _batch_last_messages 相同逻辑，但复用已有 db session。"""
    from core.i18n import _LAST_MESSAGE_FROM_MOMENT, normalize_ui_language

    lang = normalize_ui_language(lang)
    moment_preview = _LAST_MESSAGE_FROM_MOMENT.get(lang, _LAST_MESSAGE_FROM_MOMENT["zh"])

    def _display_content(role: Optional[str], content: str) -> str:
        if (role or "") == "moment":
            return moment_preview
        return content or ""

    if not companion_ids:
        return {}
    q = db.query(
        ShortTermMessageORM.companion_id,
        func.max(ShortTermMessageORM.id).label("max_id"),
    ).filter(ShortTermMessageORM.companion_id.in_(companion_ids))
    if user_id is not None:
        q = q.filter(ShortTermMessageORM.user_id == user_id)
    subq = q.group_by(ShortTermMessageORM.companion_id).subquery()
    rows = (
        db.query(ShortTermMessageORM)
        .join(subq, ShortTermMessageORM.id == subq.c.max_id)
        .all()
    )
    result = {}
    for m in rows:
        ts = m.created_at
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        result[m.companion_id] = {
            "content": _display_content(m.role, m.content or ""),
            "timestamp": ts.isoformat() if ts else datetime.now(timezone.utc).isoformat(),
        }

    # Redis 热房间可能比 PG 更新（flush 延迟）
    if user_id is not None:
        try:
            from core.chat_cache import bridge_enabled, get_recent

            if bridge_enabled():
                for cid in companion_ids:
                    try:
                        recent = get_recent(cid, n=1, user_id=user_id) or []
                        if not recent:
                            continue
                        last = recent[-1]
                        content = _display_content(last.get("role"), (last.get("content") or "").strip())
                        ts = last.get("timestamp") or ""
                        if not content:
                            continue
                        prev = result.get(cid)
                        if not prev or (ts and ts > (prev.get("timestamp") or "")):
                            result[cid] = {"content": content, "timestamp": ts}
                    except Exception:
                        continue
        except Exception:
            pass
    return _filter_last_msgs_by_cleared(result, companion_ids, user_id)


def hydrate_user_affection_turns(companion: "Companion", user_id: int) -> None:
    """从 DB 载入当前用户对某智能体的亲密度与轮数（无用户记录时回退到全局 companion_states）。"""
    with get_db() as db:
        ur = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == companion.profile.id,
            )
            .first()
        )
        if ur:
            counters = _counters_from_user_row(ur)
            aff = counters["affection"]
            turns = counters["turns"]
            fixed = catch_up_stalled_affection(aff, turns)
            if fixed > aff:
                aff = fixed
                ur.affection = aff
            companion.state.affection = aff
            companion.state.turns = turns
            return
        # 无当前用户记录：轮数/亲密度从 0 起，禁止用全局 companion_states 冒充「与我的对话」
        companion.state.affection = 0
        companion.state.turns = 0


class CompanionProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = Field(..., min_length=1, max_length=20)
    age: int = Field(..., ge=0, le=1000)
    gender: str = Field(default="女", pattern=r"^(男|女|保密)$")
    city: str = Field(..., min_length=1, max_length=64)
    # 西语等拉丁文案比中文长，上限按多语言人设留足余量；DB 列为 Text，无需迁移
    personality: str = Field(..., min_length=5, max_length=1500)
    background: str = Field(..., min_length=5, max_length=2000)
    description: str = Field(default="", max_length=1500)  # 短简介；缺省由 personality 回填，≠ background
    speech_style: str = Field(..., min_length=1, max_length=1500)
    appearance: str = Field(default="", max_length=1000)
    hobbies: str = Field(default="", max_length=1000)
    values: str = Field(default="", max_length=1000)
    fears: str = Field(default="", max_length=1000)
    love_view: str = Field(default="", max_length=1000)
    daily_routine: str = Field(default="", max_length=1500)
    favorite_things: str = Field(default="", max_length=1000)
    mbti: str = Field(default="", max_length=10)
    sexual_orientation: str = Field(default="", max_length=20)
    life_story: str = Field(default="", max_length=2000)
    cultural_values: str = Field(default="", max_length=1500)
    gender_perspective: str = Field(default="", max_length=1000)
    avatar_url: str = Field(default="", max_length=500)
    avatar_style: str = Field(default="", max_length=64)
    # 资料背景图 URL 列表（0~3）；与文本 background 无关
    profile_backgrounds: List[str] = Field(default_factory=list)
    created_by: str = Field(default="", max_length=64)
    language: str = Field(default="zh", max_length=10)
    # 人设维度轴 / 国家地区（JSON 字符串或短文本，便于复盘同 MBTI 差异）
    persona_axes: str = Field(default="", max_length=800)
    country: str = Field(default="", max_length=8)
    region_key: str = Field(default="", max_length=32)
    region_label: str = Field(default="", max_length=64)
    tier: str = Field(default="featured", max_length=20)  # s / featured / candidate / ops
    is_deleted: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# 管理端/简表创建时空人设字段占位（满足 min_length=5）
_DEFAULT_PERSONA_FIELDS = {
    "personality": "温柔体贴善于倾听",
    "background": "经历丰富内心细腻",
    "speech_style": "说话自然亲切有温度",
}

_PROFILE_BACKGROUND_MAX = 3
_PROFILE_BACKGROUND_URL_MAX = 500


def normalize_profile_backgrounds(raw) -> List[str]:
    """资料背景图：接受 URL 数组或单 URL 字符串；去空去重，最多 3 张。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        items = [s] if s else []
    elif isinstance(raw, (list, tuple)):
        items = [str(x or "").strip() for x in raw]
    else:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for u in items:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u[:_PROFILE_BACKGROUND_URL_MAX])
        if len(out) >= _PROFILE_BACKGROUND_MAX:
            break
    return out


def clamp_companion_profile_dict(data: dict) -> dict:
    """按 CompanionProfile 各字段的 MaxLen 截断字符串，避免 LLM/导入数据超长导致校验失败。
    对人设三类必填字段：空或过短时自动填默认占位，避免管理端空表单直接 400。
    """
    import json

    out = dict(data)
    if "gender" in out:
        out["gender"] = _normalize_gender(out.get("gender"))
    if "sexual_orientation" in out:
        from services.recommend_tags import canonical_orientation
        out["sexual_orientation"] = canonical_orientation(out.get("sexual_orientation"))
    # persona_axes：按 lang 的 label 原样入库，不再转成英文 key
    axes = out.get("persona_axes")
    if isinstance(axes, dict):
        out["persona_axes"] = json.dumps(axes, ensure_ascii=False)
    elif axes is None:
        out["persona_axes"] = ""
    if "profile_backgrounds" in out:
        out["profile_backgrounds"] = normalize_profile_backgrounds(
            out.get("profile_backgrounds")
        )
    for key, placeholder in _DEFAULT_PERSONA_FIELDS.items():
        val = out.get(key)
        if not isinstance(val, str) or len(val.strip()) < 5:
            out[key] = placeholder
    # description 缺省用 personality（短简介语义）；勿回退 background（成长/处境长文）
    desc = out.get("description")
    if not isinstance(desc, str) or not desc.strip():
        personality = out.get("personality")
        out["description"] = personality if isinstance(personality, str) else ""
    for name, finfo in CompanionProfile.model_fields.items():
        if name not in out:
            continue
        val = out[name]
        if not isinstance(val, str):
            continue
        max_len = None
        for m in getattr(finfo, "metadata", ()) or ():
            ml = getattr(m, "max_length", None)
            if ml is not None:
                max_len = ml
                break
        if max_len is not None and len(val) > max_len:
            out[name] = val[:max_len]
    return out


class CompanionState(BaseModel):
    mood: str = "开心"
    affection: float = 0
    summary: str = ""
    turns: int = 0
    evolved_personality: str = ""
    evolved_background: str = ""
    evolved_speech_style: str = ""


# 多 worker 下资料以 DB 为准同步到内存的字段（与 CompanionManager.update 可写字段对齐）
_PROFILE_DB_SYNC_FIELDS = (
    "name", "age", "gender", "city", "personality", "background", "description", "speech_style",
    "appearance", "hobbies", "values", "fears", "love_view", "daily_routine", "favorite_things",
    "mbti", "sexual_orientation", "life_story", "cultural_values", "gender_perspective",
    "avatar_url", "avatar_style", "profile_backgrounds", "created_by", "language", "persona_axes",
    "country", "region_key", "region_label", "tier",
)


def _profile_from_orm(row: CompanionORM) -> CompanionProfile:
    """从 CompanionORM 行构建 CompanionProfile。

    必须走 clamp：存量数据常有空 background / speech_style、超长字段等，
    直接 CompanionProfile(...) 会 ValidationError，导致 cm.get/update 误报 404。
    """
    try:
        age = int(row.age) if row.age is not None else 18
    except (TypeError, ValueError):
        age = 18
    age = max(0, min(1000, age))
    raw = {
        "id": row.id,
        "name": (row.name or "").strip() or "未命名",
        "age": age,
        "gender": _normalize_gender(row.gender or "女"),
        "city": (row.city or "").strip() or "未知",
        "personality": row.personality or "",
        "background": row.background or "",
        "description": getattr(row, "description", None) or row.personality or "",
        "speech_style": row.speech_style or "",
        "appearance": getattr(row, "appearance", None) or "",
        "hobbies": row.hobbies or "",
        "values": row.values or "",
        "fears": row.fears or "",
        "love_view": row.love_view or "",
        "daily_routine": row.daily_routine or "",
        "favorite_things": row.favorite_things or "",
        "mbti": row.mbti or "",
        "sexual_orientation": row.sexual_orientation or "",
        "life_story": row.life_story or "",
        "cultural_values": row.cultural_values or "",
        "gender_perspective": row.gender_perspective or "",
        "avatar_url": row.avatar_url or "",
        "avatar_style": getattr(row, "avatar_style", None) or "",
        "profile_backgrounds": normalize_profile_backgrounds(
            getattr(row, "profile_backgrounds", None)
        ),
        "created_by": row.created_by or "",
        "language": row.language or "zh",
        "persona_axes": getattr(row, "persona_axes", None) or "",
        "country": getattr(row, "country", None) or "",
        "region_key": getattr(row, "region_key", None) or "",
        "region_label": getattr(row, "region_label", None) or "",
        "tier": (getattr(row, "tier", None) or "featured"),
        "created_at": row.created_at.isoformat()
        if row.created_at
        else datetime.now(timezone.utc).isoformat(),
        "is_deleted": bool(getattr(row, "is_deleted", False)),
    }
    profile = CompanionProfile(**clamp_companion_profile_dict(raw))
    profile.is_deleted = bool(getattr(row, "is_deleted", False))
    return profile


def _apply_orm_row_to_profile(profile: CompanionProfile, row: CompanionORM) -> None:
    """把 ORM 行上的资料字段写回内存 profile（原地更新，保留同一对象引用）。"""
    for key in _PROFILE_DB_SYNC_FIELDS:
        if key == "age":
            val = row.age if row.age is not None else 18
        elif key == "gender":
            val = _normalize_gender(row.gender or "女")
        elif key == "city":
            val = row.city or "未知"
        elif key == "personality":
            val = row.personality or "温柔体贴"
        elif key == "description":
            val = getattr(row, "description", None) or row.personality or ""
        elif key == "language":
            val = row.language or "zh"
        elif key == "tier":
            val = getattr(row, "tier", None) or "featured"
        elif key == "profile_backgrounds":
            val = normalize_profile_backgrounds(
                getattr(row, "profile_backgrounds", None)
            )
        else:
            val = getattr(row, key, None) or ""
        setattr(profile, key, val)
    profile.is_deleted = bool(getattr(row, "is_deleted", False))


class Companion:
    def __init__(self, profile: CompanionProfile, memory_root: str, state: Optional[CompanionState] = None):
        self.profile = profile
        self.dir = os.path.join(memory_root, profile.id)
        os.makedirs(self.dir, exist_ok=True)
        # 未绑定用户的占位；热路径必须用 memory_for(user_id)，禁止对共享实例 bind_user
        self.memory = CompanionMemory(profile.id, self.dir, user_id=None)
        self.state = state if state is not None else self._load_state()
        self._memory_by_user: Dict[int, CompanionMemory] = {}
        self._memory_lock = threading.Lock()

    def memory_for(self, user_id: Optional[int]) -> CompanionMemory:
        """按用户返回独立记忆句柄，避免多用户并发 bind_user 串写 STM。"""
        if user_id is None:
            return CompanionMemory(self.profile.id, self.dir, user_id=None)
        uid = int(user_id)
        with self._memory_lock:
            hit = self._memory_by_user.get(uid)
            if hit is None:
                hit = CompanionMemory(self.profile.id, self.dir, user_id=uid)
                self._memory_by_user[uid] = hit
                # 简单上限，防止长驻进程无限涨
                if len(self._memory_by_user) > 512:
                    # 删掉任意一个非当前 uid（旧句柄无状态，下次按需重建）
                    for k in list(self._memory_by_user.keys()):
                        if k != uid:
                            self._memory_by_user.pop(k, None)
                            break
            return hit

    def _load_state(self) -> CompanionState:
        with get_db() as db:
            row = db.query(CompanionStateORM).filter(
                CompanionStateORM.companion_id == self.profile.id
            ).first()
            if row:
                return CompanionState(
                    mood=row.mood or "开心",
                    affection=row.affection if row.affection is not None else 0,
                    summary=row.summary or "",
                    turns=row.turns if row.turns is not None else 0,
                    evolved_personality=row.evolved_personality or "",
                    evolved_background=row.evolved_background or "",
                    evolved_speech_style=row.evolved_speech_style or "",
                )
            # 没有则创建默认状态
            db.add(CompanionStateORM(companion_id=self.profile.id))
            return CompanionState()

    def save_state(self, user_id: Optional[int] = None):
        now = datetime.now(timezone.utc)
        with get_db() as db:
            row = db.query(CompanionStateORM).filter(
                CompanionStateORM.companion_id == self.profile.id
            ).first()
            if row:
                row.mood = self.state.mood
                row.summary = self.state.summary
                row.evolved_personality = self.state.evolved_personality
                row.evolved_background = self.state.evolved_background
                row.evolved_speech_style = self.state.evolved_speech_style
                if user_id is None:
                    row.affection = self.state.affection
                    row.turns = self.state.turns
            else:
                db.add(
                    CompanionStateORM(
                        companion_id=self.profile.id,
                        mood=self.state.mood,
                        affection=self.state.affection if user_id is None else 0,
                        summary=self.state.summary,
                        turns=self.state.turns if user_id is None else 0,
                        evolved_personality=self.state.evolved_personality,
                        evolved_background=self.state.evolved_background,
                        evolved_speech_style=self.state.evolved_speech_style,
                    )
                )

            if user_id is not None:
                ur = (
                    db.query(UserCompanionStateORM)
                    .filter(
                        UserCompanionStateORM.user_id == user_id,
                        UserCompanionStateORM.companion_id == self.profile.id,
                    )
                    .first()
                )
                if ur:
                    ur.affection = self.state.affection
                    ur.turns = self.state.turns
                    ur.updated_at = now
                else:
                    db.add(
                        UserCompanionStateORM(
                            user_id=user_id,
                            companion_id=self.profile.id,
                            affection=self.state.affection,
                            turns=self.state.turns,
                            updated_at=now,
                        )
                    )

    def refresh_profile_from_db(self, db=None) -> None:
        """以 DB 资料为准同步到内存。

        多 worker 下 update 只更新「处理请求的那个进程」内存；其它进程若只读缓存
        会继续返回旧 name/personality 等，表现为「改了又改回去」。
        """
        def _read(session) -> None:
            row = (
                session.query(CompanionORM)
                .filter(CompanionORM.id == self.profile.id)
                .first()
            )
            if row is not None:
                _apply_orm_row_to_profile(self.profile, row)

        if db is not None:
            _read(db)
        else:
            with get_db() as session:
                _read(session)

    def refresh_avatar_from_db(self, db=None) -> str:
        """以 DB 的 avatar_url 为准同步到内存（兼容旧调用；内部走完整资料刷新）。"""
        self.refresh_profile_from_db(db)
        return self.profile.avatar_url or ""

    def to_dict(
        self,
        user_id: Optional[int] = None,
        user_state: Optional[dict] = None,
        *,
        sync_profile: bool = True,
    ) -> dict:
        state_payload = self.state.model_dump(exclude={"mood"})
        with get_db() as db:
            # 详情/聊天等对外响应：资料一律以 DB 为准（多 worker 缓存一致性）
            if sync_profile:
                self.refresh_profile_from_db(db)
            avatar = self.profile.avatar_url or ""
            if user_state is not None:
                counters = {
                    "affection": user_state.get("affection", 0),
                    "turns": user_state.get("turns", 0),
                }
                state_payload["affection"] = float(counters["affection"] or 0)
                state_payload["turns"] = int(counters["turns"] or 0)
            elif user_id is not None:
                ur = (
                    db.query(UserCompanionStateORM)
                    .filter(
                        UserCompanionStateORM.user_id == user_id,
                        UserCompanionStateORM.companion_id == self.profile.id,
                    )
                    .first()
                )
                counters = _counters_from_user_row(ur)
                state_payload["affection"] = counters["affection"]
                state_payload["turns"] = counters["turns"]
        return {
            "profile": self.profile.model_dump(),
            "state": state_payload,
            "avatar": avatar,
            "avatar_generating": avatar == "__GENERATING__",
        }


class CompanionManager:
    def __init__(self, memory_root: str = "memory"):
        self.memory_root = memory_root
        os.makedirs(self.memory_root, exist_ok=True)
        self._companions: dict[str, Companion] = {}
        self._load_all()

    def _load_all(self):
        try:
            with get_db() as db:
                rows = db.query(CompanionORM).all()
                # 批量查询所有 companion 状态，避免 N+1
                state_rows = db.query(CompanionStateORM).all()
                state_map = {s.companion_id: s for s in state_rows}
                for row in rows:
                    try:
                        profile = _profile_from_orm(row)
                        s = state_map.get(row.id)
                        state = CompanionState(
                            mood=s.mood or "开心",
                            affection=s.affection if s.affection is not None else 0,
                            summary=s.summary or "",
                            turns=s.turns if s.turns is not None else 0,
                            evolved_personality=s.evolved_personality or "",
                            evolved_background=s.evolved_background or "",
                            evolved_speech_style=s.evolved_speech_style or "",
                        ) if s else None
                        self._companions[profile.id] = Companion(profile, self.memory_root, state)
                    except Exception as e:
                        logger.error("[CompanionManager] 加载智能体 %s (%s) 失败: %s。跳过此智能体继续加载...", row.id, row.name or "未知", str(e))
                        continue  # 关键修复：单个智能体失败不影响整体加载
        except Exception as e:
            logger.warning("[CompanionManager] 加载智能体列表失败（数据库可能尚未初始化）: %s", e)

    def create(self, profile_data: dict, chat_history: list = None) -> Companion:
        raw = dict(profile_data or {})
        extra_ops = raw.pop("tags", None)
        from services.recommend_tags import sync_companion_tags
        profile = CompanionProfile(**clamp_companion_profile_dict(raw))

        # 语言与城市：仅在未显式指定 language 时按城市推断；显式指定则尊重（含 zh）
        explicit_lang = bool((profile_data.get("language") or "").strip())
        if not explicit_lang:
            profile.language = infer_language_from_city(profile.city)
        elif not (profile.language or "").strip():
            profile.language = infer_language_from_city(profile.city)

        # 内存中去重（名称+城市）
        for c in self._companions.values():
            if c.profile.name == profile.name and c.profile.city == profile.city:
                return c

        with get_db() as db:
            # 数据库中去重：id 或 name+city 已存在则返回已有记录
            existing = db.query(CompanionORM).filter(
                (CompanionORM.id == profile.id) |
                ((CompanionORM.name == profile.name) & (CompanionORM.city == profile.city))
            ).first()
            if existing:
                # 如果内存中没有但 DB 中有，重新加载到内存
                if existing.id not in self._companions:
                    self._load_all()
                hit = self._companions.get(existing.id)
                if hit:
                    return hit
                return Companion(
                    CompanionProfile(
                        id=existing.id,
                        name=existing.name,
                        age=existing.age or 18,
                        gender=_normalize_gender(existing.gender or "女"),
                        city=existing.city or "未知",
                        personality=existing.personality or "温柔体贴",
                        background=existing.background or "",
                        description=getattr(existing, "description", None) or existing.personality or "",
                        speech_style=existing.speech_style or "",
                        appearance=getattr(existing, "appearance", None) or "",
                        hobbies=existing.hobbies or "",
                        values=existing.values or "",
                        fears=existing.fears or "",
                        love_view=existing.love_view or "",
                        daily_routine=existing.daily_routine or "",
                        favorite_things=existing.favorite_things or "",
                        mbti=existing.mbti or "",
                        sexual_orientation=existing.sexual_orientation or "",
                        life_story=existing.life_story or "",
                        cultural_values=existing.cultural_values or "",
                        gender_perspective=existing.gender_perspective or "",
                        avatar_url=existing.avatar_url or "",
                        avatar_style=getattr(existing, "avatar_style", None) or "",
                        created_by=existing.created_by or "",
                        language=existing.language or "zh",
                        persona_axes=getattr(existing, "persona_axes", None) or "",
                        country=getattr(existing, "country", None) or "",
                        region_key=getattr(existing, "region_key", None) or "",
                        region_label=getattr(existing, "region_label", None) or "",
                        created_at=existing.created_at.isoformat() if existing.created_at else datetime.now(timezone.utc).isoformat(),
                    ),
                    self.memory_root,
                )

            db.add(CompanionORM(
                id=profile.id,
                name=profile.name,
                age=profile.age,
                gender=profile.gender,
                city=profile.city,
                personality=profile.personality,
                background=profile.background,
                description=getattr(profile, "description", "") or profile.personality or "",
                speech_style=profile.speech_style,
                appearance=getattr(profile, "appearance", "") or "",
                hobbies=profile.hobbies,
                values=profile.values,
                fears=profile.fears,
                love_view=profile.love_view,
                daily_routine=profile.daily_routine,
                favorite_things=profile.favorite_things,
                mbti=profile.mbti,
                sexual_orientation=profile.sexual_orientation,
                life_story=profile.life_story,
                cultural_values=profile.cultural_values,
                gender_perspective=profile.gender_perspective,
                avatar_url=profile.avatar_url,
                avatar_style=getattr(profile, "avatar_style", "") or "",
                profile_backgrounds=normalize_profile_backgrounds(
                    getattr(profile, "profile_backgrounds", None)
                ),
                created_by=profile.created_by or "",
                language=profile.language or "zh",
                persona_axes=getattr(profile, "persona_axes", "") or "",
                country=getattr(profile, "country", "") or "",
                region_key=getattr(profile, "region_key", "") or "",
                region_label=getattr(profile, "region_label", "") or "",
                tier=(getattr(profile, "tier", None) or "featured"),
                tags=sync_companion_tags(
                    profile.personality,
                    profile.gender,
                    extra_ops=extra_ops,
                    sexual_orientation=profile.sexual_orientation,
                ),
                created_at=datetime.fromisoformat(profile.created_at) if profile.created_at else datetime.now(timezone.utc),
            ))
            db.add(CompanionStateORM(companion_id=profile.id))

        companion = Companion(profile, self.memory_root)
        self._companions[profile.id] = companion

        # 导入聊天记录（写入创建者房间，避免落到无 user 的共享句柄）
        if chat_history:
            owner_uid = None
            raw_by = (profile.created_by or "").strip()
            if raw_by.isdigit():
                owner_uid = int(raw_by)
            mem = companion.memory_for(owner_uid) if owner_uid is not None else companion.memory
            for msg in chat_history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and content:
                    mem.add_user_message(content)
                elif role == "assistant" and content:
                    mem.add_assistant_message(content)

        # 标记头像待生成，由调用方启动后台异步任务
        if not profile.avatar_url:
            profile.avatar_url = "__GENERATING__"
            with get_db() as db:
                row = db.query(CompanionORM).filter(CompanionORM.id == profile.id).first()
                if row:
                    row.avatar_url = "__GENERATING__"

        try:
            from services.companion_cache_bridge import (
                ACTION_CREATE,
                publish_companion_invalidate,
            )

            publish_companion_invalidate(profile.id, action=ACTION_CREATE)
        except Exception:
            logger.debug(
                "companion cache invalidate after create skipped id=%s",
                profile.id,
                exc_info=True,
            )

        return companion

    def get(self, companion_id: str, include_deleted: bool = False) -> Optional[Companion]:
        companion = self._companions.get(companion_id)
        if companion:
            # 多 worker：缓存命中也回刷 DB，避免其它进程 update 后本进程仍返回旧资料
            companion.refresh_profile_from_db()
            if companion.profile.is_deleted and not include_deleted:
                return None
            return companion

        # 缓存未命中，查数据库兜底（脚本直接写库、create 竞态等场景）
        try:
            from core.database import get_db, CompanionORM, CompanionStateORM
            with get_db() as db:
                q = db.query(CompanionORM).filter(CompanionORM.id == companion_id)
                if not include_deleted:
                    q = q.filter(CompanionORM.is_deleted == False)
                row = q.first()
                if not row:
                    return None
                profile = _profile_from_orm(row)
                s = db.query(CompanionStateORM).filter(CompanionStateORM.companion_id == companion_id).first()
                state = CompanionState(
                    mood=s.mood or "开心",
                    affection=s.affection if s.affection is not None else 0,
                    summary=s.summary or "",
                    turns=s.turns if s.turns is not None else 0,
                    evolved_personality=s.evolved_personality or "",
                    evolved_background=s.evolved_background or "",
                    evolved_speech_style=s.evolved_speech_style or "",
                ) if s else None
                companion = Companion(profile, self.memory_root, state)
                self._companions[companion_id] = companion  # 回填缓存
                logger.info("[CompanionManager] 缓存未命中，从数据库加载: %s (%s)", companion_id, row.name)
                return companion
        except Exception as e:
            logger.error("[CompanionManager] DB 兜底查询失败 %s: %s", companion_id, e)
            return None

    def list_all(self, user_id: Optional[int] = None, lang: str = "zh") -> List[Dict]:
        """返回全部智能体（业务：可见别人创建的智能体）。

        - 传入 user_id：附带该用户亲密度 / 末条消息预览；自己软删的隐藏，别人软删的仍可见
        - 不传 user_id：Admin 全量列表（无用户态，默认不含已软删）
        """
        result = []
        if user_id is not None:
            owner_keys = self._owner_keys(user_id)
            matched: List[tuple] = []
            for c in self._companions.values():
                if c.profile.is_deleted:
                    cb = (c.profile.created_by or "").strip()
                    if cb in owner_keys:
                        continue  # 创建者侧：自己软删的不可见
                matched.append((c, c.profile.id))
        else:
            matched = [(c, c.profile.id) for c in self._companions.values() if not c.profile.is_deleted]
        companion_ids = [cid for _, cid in matched]
        last_msgs = _batch_last_messages(companion_ids, user_id=user_id, lang=lang)
        state_map = (
            _batch_user_companion_states(user_id, companion_ids) if user_id is not None else {}
        )
        for c, cid in matched:
            item = c.to_dict(
                user_id=user_id,
                user_state=state_map.get(cid) if user_id is not None else None,
                sync_profile=False,
            )
            last = last_msgs.get(cid)
            if last:
                item["last_message"] = last["content"]
                item["last_message_time"] = last["timestamp"]
            else:
                item["last_message"] = ""
                item["last_message_time"] = ""
            result.append(item)
        return result

    def update(self, companion_id: str, data: dict) -> Optional[Companion]:
        # 走 get 兜底：本 worker 缓存未命中时从 DB 加载，避免其它 worker 创建后本进程 update 直接 404
        companion = self.get(companion_id, include_deleted=True)
        if not companion:
            return None

        # 更新内存中的 profile（含 persona_axes / country / region_key）
        updatable = {
            "name", "age", "gender", "city", "personality", "background", "description", "speech_style", "appearance",
            "hobbies", "values", "fears", "love_view", "daily_routine", "favorite_things",
            "mbti", "sexual_orientation", "life_story", "cultural_values", "gender_perspective",
            "avatar_url", "avatar_style", "profile_backgrounds", "created_by", "language",
            "persona_axes", "country", "region_key", "region_label", "tier",
        }
        for key in updatable:
            if key in data:
                val = data[key]
                if key == "gender":
                    val = _normalize_gender(val)
                if key == "persona_axes" and isinstance(val, dict):
                    import json as _json
                    val = _json.dumps(val, ensure_ascii=False)
                if key == "profile_backgrounds":
                    val = normalize_profile_backgrounds(val)
                setattr(companion.profile, key, val)

        # 如果更新了 city 或 language，确保一致性（不强改用户显式指定的非推断语言）
        if "city" in data or "language" in data:
            city = getattr(companion.profile, 'city', '')
            lang = getattr(companion.profile, 'language', '') or ''
            inferred = infer_language_from_city(city)
            # 仅在未设置 language，或 language 与城市一致时才强制；显式 language 已写且 city 未变则保留
            if "language" not in data:
                if not lang or lang != inferred:
                    companion.profile.language = inferred
                    logger.info(
                        "[CompanionManager] Updated language for %s to %s based on city %s",
                        companion.profile.name,
                        inferred,
                        city,
                    )
            # language 已在 patch 中显式传入时以用户为准，不因改 city 强行覆盖

        # 同步更新 PostgreSQL（含内存中可能已推断的 language / 规范化字段）
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if row:
                for key in updatable:
                    if key in data or key in ("language", "persona_axes", "country", "region_key", "region_label", "gender"):
                        if key not in data and key == "language":
                            setattr(row, key, getattr(companion.profile, key, None))
                            continue
                        if key not in data:
                            continue
                        val = data[key]
                        if key == "gender":
                            val = _normalize_gender(val)
                        if key == "persona_axes" and isinstance(val, dict):
                            import json as _json
                            val = _json.dumps(val, ensure_ascii=False)
                        if key == "profile_backgrounds":
                            from sqlalchemy.orm.attributes import flag_modified as _flag_bg

                            val = normalize_profile_backgrounds(val)
                            setattr(row, key, val)
                            _flag_bg(row, "profile_backgrounds")
                            continue
                        setattr(row, key, val)
                # 确保 city 变更后的 language 落库
                row.language = getattr(companion.profile, "language", row.language)
                extra_ops = data["tags"] if "tags" in data else None
                from sqlalchemy.orm.attributes import flag_modified as _flag_modified
                from services.recommend_tags import sync_companion_tags as _sync_tags
                row.tags = _sync_tags(
                    row.personality,
                    row.gender,
                    existing=getattr(row, "tags", None),
                    extra_ops=extra_ops,
                    sexual_orientation=row.sexual_orientation,
                )
                _flag_modified(row, "tags")

        # 处理 system_prompt_* 和 agent 配置，保存到独立表（修复前端保存不生效问题）
        prompt_data = {}
        for k, v in data.items():
            if k.startswith("system_prompt_") or k in ("temperature", "max_tokens", "top_p"):
                prompt_data[k] = v
        if prompt_data:
            with get_db() as db:
                config_row = db.query(CompanionAgentConfigORM).filter(
                    CompanionAgentConfigORM.companion_id == companion_id
                ).first()
                if config_row:
                    cfg = dict(config_row.config_json or {})
                    cfg.update(prompt_data)
                    config_row.config_json = cfg
                else:
                    db.add(CompanionAgentConfigORM(
                        companion_id=companion_id,
                        config_json=prompt_data
                    ))

        try:
            from services.agent_utils import invalidate_agent_config_cache
            from services.companion_cache_bridge import (
                ACTION_REFRESH,
                publish_companion_invalidate,
            )

            if prompt_data:
                invalidate_agent_config_cache(companion_id)
            publish_companion_invalidate(companion_id, action=ACTION_REFRESH)
        except Exception:
            logger.debug(
                "companion cache invalidate after update skipped id=%s",
                companion_id,
                exc_info=True,
            )

        return companion

    def delete(self, companion_id: str) -> bool:
        companion = self._companions.get(companion_id)
        if not companion:
            # 缓存没有，查数据库兜底
            from core.database import CompanionORM as _CompORM
            with get_db() as db:
                if not db.query(_CompORM.id).filter(_CompORM.id == companion_id).first():
                    return False

        # 先删数据库，成功后再清缓存
        with get_db() as db:
            # 先删除该智能体在所有朋友圈下的评论和点赞（包括别人的朋友圈）
            db.query(MomentCommentORM).filter(
                MomentCommentORM.companion_id == companion_id
            ).delete(synchronize_session=False)

            # 再删除该智能体自己发布的朋友圈及其关联的评论和点赞
            moment_ids = [
                m.id for m in db.query(MomentORM.id).filter(
                    MomentORM.companion_id == companion_id
                ).all()
            ]
            if moment_ids:
                db.query(MomentCommentORM).filter(
                    MomentCommentORM.moment_id.in_(moment_ids)
                ).delete(synchronize_session=False)
                db.query(MomentLikeORM).filter(
                    MomentLikeORM.moment_id.in_(moment_ids)
                ).delete(synchronize_session=False)
                db.query(MomentORM).filter(
                    MomentORM.companion_id == companion_id
                ).delete(synchronize_session=False)
            # 删除其他关联数据
            db.query(ShortTermMessageORM).filter(
                ShortTermMessageORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            db.query(FactORM).filter(
                FactORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            db.query(RelationSummaryORM).filter(
                RelationSummaryORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            db.query(CompanionAgentConfigORM).filter(
                CompanionAgentConfigORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            db.query(UserCompanionStateORM).filter(
                UserCompanionStateORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            db.query(CompanionStateORM).filter(
                CompanionStateORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            # 清理系统通知中引用该智能体的记录
            db.query(SystemNotificationORM).filter(
                SystemNotificationORM.companion_id == companion_id
            ).delete(synchronize_session=False)
            # 取消该智能体所有待执行的运营触达任务
            db.query(OutreachJobORM).filter(
                OutreachJobORM.companion_id == companion_id,
                OutreachJobORM.status.in_(["pending", "processing"]),
            ).update(
                {"status": "cancelled", "skip_reason": "companion_deleted"},
                synchronize_session=False,
            )
            db.query(CompanionORM).filter(
                CompanionORM.id == companion_id
            ).delete(synchronize_session=False)

        # DB 删除成功后，再清缓存
        self._companions.pop(companion_id, None)
        if companion:
            shutil.rmtree(companion.dir, ignore_errors=True)
        try:
            from services.companion_cache_bridge import (
                ACTION_DELETE,
                publish_companion_invalidate,
            )

            publish_companion_invalidate(companion_id, action=ACTION_DELETE)
        except Exception:
            logger.debug(
                "companion cache invalidate after delete skipped id=%s",
                companion_id,
                exc_info=True,
            )
        return True

    def get_or_create(self, companion_id: Optional[str], profile_data: Optional[dict] = None) -> Optional[Companion]:
        if companion_id:
            return self.get(companion_id)
        if profile_data:
            return self.create(profile_data)
        return None

    def clear_all(self) -> int:
        """清除所有智能体及其关联数据（朋友圈、记忆、知识库等），返回删除的数量"""
        count = len(self._companions)
        self._companions.clear()

        # 清空内存目录
        if os.path.exists(self.memory_root):
            for item in os.listdir(self.memory_root):
                item_path = os.path.join(self.memory_root, item)
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
                    else:
                        os.unlink(item_path)
                except Exception as e:
                    logger.warning("[CompanionManager] 清理内存目录 %s 失败: %s", item_path, e)

        with get_db() as db:
            # 按依赖顺序删除（避免外键约束问题）
            # 1. 点赞、评论、朋友圈
            db.query(MomentLikeORM).delete(synchronize_session=False)
            db.query(MomentCommentORM).delete(synchronize_session=False)
            db.query(MomentORM).delete(synchronize_session=False)

            # 2. 其他关联数据
            db.query(ShortTermMessageORM).delete(synchronize_session=False)
            db.query(FactORM).delete(synchronize_session=False)
            db.query(RelationSummaryORM).delete(synchronize_session=False)
            db.query(CompanionAgentConfigORM).delete(synchronize_session=False)
            db.query(UserCompanionStateORM).delete(synchronize_session=False)
            db.query(CompanionStateORM).delete(synchronize_session=False)

            # 3. 智能体本身
            deleted = db.query(CompanionORM).delete(synchronize_session=False)

            # 4. 清空知识库（向量和元数据）
            try:
                from services.knowledge_base import knowledge_base
                knowledge_base.clear_all()
            except Exception as e:
                logger.warning("[CompanionManager] 清空知识库失败: %s", e)

            db.commit()
            logger.info("[CompanionManager] 已清除所有 %s 个智能体及其全部关联数据", deleted or count)
            return deleted or count

    def soft_delete(self, companion_id: str) -> bool:
        """软删除：标记 is_deleted=True，并取消待执行的触达任务。支持缓存未命中时回源 DB。"""
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if not row:
                return False
            row.is_deleted = True
            # 取消该智能体所有待执行的运营触达任务，避免已删除智能体仍主动发消息
            db.query(OutreachJobORM).filter(
                OutreachJobORM.companion_id == companion_id,
                OutreachJobORM.status.in_(["pending", "processing"]),
            ).update(
                {"status": "cancelled", "skip_reason": "companion_soft_deleted"},
                synchronize_session=False,
            )
            db.commit()

        companion = self._companions.get(companion_id)
        if companion:
            companion.profile.is_deleted = True
        try:
            from services.companion_cache_bridge import (
                ACTION_REFRESH,
                publish_companion_invalidate,
            )

            publish_companion_invalidate(companion_id, action=ACTION_REFRESH)
        except Exception:
            logger.debug(
                "companion cache invalidate after soft_delete skipped id=%s",
                companion_id,
                exc_info=True,
            )
        return True

    def restore(self, companion_id: str) -> bool:
        """恢复软删除。支持缓存未命中时回源 DB。"""
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if not row:
                return False
            row.is_deleted = False
            db.commit()

        companion = self._companions.get(companion_id)
        if companion:
            companion.profile.is_deleted = False
        else:
            # 回填缓存，便于后续 get()
            self.get(companion_id, include_deleted=False)
        try:
            from services.companion_cache_bridge import (
                ACTION_REFRESH,
                publish_companion_invalidate,
            )

            publish_companion_invalidate(companion_id, action=ACTION_REFRESH)
        except Exception:
            logger.debug(
                "companion cache invalidate after restore skipped id=%s",
                companion_id,
                exc_info=True,
            )
        return True

    def _get_user_info(self, user_id: int) -> dict:
        """获取用户基本信息"""
        with get_db() as db:
            user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                return {"id": user.id, "username": user.username or ""}
        return {"id": user_id, "username": ""}

    def _owner_keys(self, user_id: int, db=None) -> set:
        """创建者匹配键：user_id / username / nickname（列表可见性与 is_created 一致）。"""
        keys = {str(user_id)}
        def _fill(session):
            user = session.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                if (user.username or "").strip():
                    keys.add(user.username.strip())
                if (user.nickname or "").strip():
                    keys.add(user.nickname.strip())
        if db is not None:
            _fill(db)
        else:
            with get_db() as session:
                _fill(session)
        return keys

    @staticmethod
    def soft_delete_visibility_clause(CompanionORM, owner_keys: Optional[set]):
        """软删可见性：未删除 → 全员可见；已删除 → 仅非创建者可见（创建者侧隐藏）。"""
        from sqlalchemy import or_

        if not owner_keys:
            return CompanionORM.is_deleted.isnot(True)
        return or_(
            CompanionORM.is_deleted.isnot(True),
            ~CompanionORM.created_by.in_(list(owner_keys)),
        )

    def _is_my_companion(self, companion, user_info: dict) -> bool:
        """判断是否是自己创建的智能体"""
        if not companion:
            return False
        cb = (companion.profile.created_by or "").strip()
        if not cb:
            return True  # 无创建者信息，允许操作
        if cb == str(user_info.get("id")):
            return True
        if cb == user_info.get("username"):
            return True
        return False

    def is_soft_deleted(self, companion_id: str) -> bool:
        """是否已软删。优先内存，再查库（多 worker 时以 DB 为准）。"""
        companion = self._companions.get(companion_id)
        if companion and companion.profile.is_deleted:
            return True
        with get_db() as db:
            row = (
                db.query(CompanionORM.is_deleted)
                .filter(CompanionORM.id == companion_id)
                .first()
            )
            if not row:
                return False
            deleted = bool(row[0])
        if deleted and companion:
            companion.profile.is_deleted = True
        return deleted

    def is_owner(self, companion, user_id: int) -> bool:
        """当前用户是否为创建者（与 _assert_companion_owner 一致：id / username）。"""
        if not companion or user_id is None:
            return False
        cb = (companion.profile.created_by or "").strip()
        if not cb:
            return False
        if cb == str(user_id):
            return True
        with get_db() as db:
            user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                uname = (user.username or "").strip()
                if uname and cb == uname:
                    return True
        return False

    def list_all_for_any(
        self,
        user_id: int,
        special: Optional[str] = None,
        intimacy_value: Optional[float] = None,
        intimacy_gt: bool = False,
        min_turns: int = 0,
        is_created: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
        lang: str = "zh",
        match: str = "prefer",
        filter_lang: str = "",
        gender: str = "",
        orientation: str = "",
        recommend_debug: Optional[dict] = None,
        sort: Optional[str] = None,
    ) -> tuple:
        """获取智能体列表（支持多种筛选）"""

        # 消息场景：直接 SQL 查询优化
        if special == "message":
            return self._list_message_scene(user_id, limit, offset, lang=lang, sort=sort)

        # 推荐场景：按全站亲密度总和 + 聊天持续时长排序
        if special == "recommend":
            return self._list_recommend_scene(
                user_id, limit, offset, lang=lang, match=match,
                filter_lang=filter_lang, gender=gender, orientation=orientation,
                recommend_debug=recommend_debug,
            )

        # 其他场景：SQL 查询优化
        return self._list_other_scenes(
            user_id,
            intimacy_value,
            is_created,
            limit,
            offset,
            intimacy_gt=intimacy_gt,
            min_turns=min_turns,
            lang=lang,
            match=match,
            filter_lang=filter_lang,
            gender=gender,
            orientation=orientation,
            recommend_debug=recommend_debug,
        )

    def _apply_list_waterfall(
        self,
        db,
        query,
        *,
        user_id: int,
        lang: str,
        special: Optional[str] = None,
        is_created: Optional[bool] = None,
        intimacy_value: Optional[float] = None,
        min_turns: int = 0,
        intimacy_gt: bool = False,
        filter_lang: str = "",
        gender: str = "",
        orientation: str = "",
        recommend_debug: Optional[dict] = None,
    ):
        from services.recommend import (
            apply_agent_tag_filters,
            apply_hard_filters,
            apply_waterfall_to_companion_query,
            load_tag_def_map,
            resolve_companion_list_lang,
            resolve_for_request,
            should_apply_waterfall,
        )
        from services.recommend_tags import waterfall_surface
        from core.database import CompanionORM

        if not should_apply_waterfall(
            special=special,
            is_created=is_created,
            intimacy_value=intimacy_value,
            intimacy_gt=intimacy_gt,
            min_turns=min_turns,
        ):
            query = apply_hard_filters(query, CompanionORM, filter_lang, gender, orientation)
            if recommend_debug is not None:
                recommend_debug.update({
                    "matched_strategy_id": None,
                    "fallback_layer": None,
                    "skipped": True,
                })
            return query

        tag_def_map = load_tag_def_map()
        hard_lang = resolve_companion_list_lang(
            db, user_id, filter_lang=filter_lang, accept_lang=lang
        )

        def count_fn(agent_tags, agent_match, lang_eq):
            q = apply_agent_tag_filters(query, CompanionORM.tags, agent_tags, tag_def_map, agent_match)
            use_lang = lang_eq or hard_lang
            if use_lang:
                q = apply_hard_filters(q, CompanionORM, use_lang, "", "")
            return int(q.count() or 0)

        pool = resolve_for_request(
            db,
            surface=waterfall_surface(special=special),
            user_id=user_id,
            ui_lang=lang,
            count_fn=count_fn,
            logged_in=True,
        )
        query = apply_waterfall_to_companion_query(query, pool, tag_def_map, CompanionORM)
        query = apply_hard_filters(query, CompanionORM, hard_lang or filter_lang, gender, orientation)
        if recommend_debug is not None:
            recommend_debug.update(pool.as_debug())
            recommend_debug["_pool"] = pool
            recommend_debug["_tag_def_map"] = tag_def_map
        return query

    def _list_other_scenes(
        self,
        user_id: int,
        intimacy_value: Optional[float] = None,
        is_created: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
        intimacy_gt: bool = False,
        min_turns: int = 0,
        lang: str = "zh",
        match: str = "prefer",
        filter_lang: str = "",
        gender: str = "",
        orientation: str = "",
        recommend_debug: Optional[dict] = None,
    ) -> tuple:
        """其他场景：全部套 companions_all 策略；机器人页 / 我的 / 恋人 / 亲密度记录不套策略。
        使用 JOIN 一次查询完成，避免 N+1

        intimacy_value:
          - None / 0：不按亲密度数值过滤（兼容首页传 0）
          - >0：按阈值过滤；intimacy_gt 控制 > 或 >=
          - affection=0：可用 min_turns>=1 筛出聊过天的（turns>0）
          - 清空聊天后 turns=0 但 affection>0：min_turns>=1 仍应返回
        """
        from core.database import get_db, CompanionORM, UserCompanionStateORM, CompanionStateORM
        from sqlalchemy import or_, func
        from services.companion_report import list_excluded_companion_ids_for_user

        with get_db() as db:
            owner_keys = self._owner_keys(user_id, db=db)

            excluded_ids = list_excluded_companion_ids_for_user(
                db, user_id, include_outreach=(is_created is not True)
            )

            # 使用 LEFT JOIN 一次性查出 companions + user_companion_states + companion_states
            query = db.query(
                CompanionORM,
                func.coalesce(UserCompanionStateORM.affection, 0).label("user_affection"),
                func.coalesce(UserCompanionStateORM.turns, 0).label("user_turns"),
                CompanionStateORM.summary.label("comp_summary"),
            ).outerjoin(
                UserCompanionStateORM,
                (UserCompanionStateORM.companion_id == CompanionORM.id) &
                (UserCompanionStateORM.user_id == user_id)
            ).outerjoin(
                CompanionStateORM,
                CompanionStateORM.companion_id == CompanionORM.id
            ).filter(
                # 自己软删的隐藏；别人软删的仍可见
                self.soft_delete_visibility_clause(CompanionORM, owner_keys),
                ~CompanionORM.id.in_(excluded_ids) if excluded_ids else True,
            )

            # is_created 筛选：兼容历史 created_by 存 user_id / username / nickname
            if is_created is True:
                query = query.filter(CompanionORM.created_by.in_(list(owner_keys)))

            # intimacy_value 筛选：
            # - None / 不传 / 0：不按亲密度过滤（兼容首页 intimacy_value=0）
            # - >0：按 >= 或 >（intimacy_gt）过滤
            # affection=0 仍可用 min_turns>=1 筛出有过对话的会话
            # 清空聊天后 turns=0、affection>0 也应出现在亲密度记录
            if intimacy_value is not None and intimacy_value > 0:
                if intimacy_gt:
                    query = query.filter(
                        UserCompanionStateORM.affection > intimacy_value
                    )
                else:
                    query = query.filter(
                        UserCompanionStateORM.affection >= intimacy_value
                    )
            elif intimacy_gt and intimacy_value is not None:
                # intimacy_value=0 + intimacy_gt → affection > 0
                query = query.filter(
                    UserCompanionStateORM.affection > intimacy_value
                )

            if min_turns and min_turns > 0:
                query = query.filter(
                    or_(
                        func.coalesce(UserCompanionStateORM.turns, 0) >= min_turns,
                        func.coalesce(UserCompanionStateORM.affection, 0) > 0,
                    )
                )

            _waterfall_debug: dict = {}
            query = self._apply_list_waterfall(
                db,
                query,
                user_id=user_id,
                lang=lang,
                special=None,
                is_created=is_created,
                intimacy_value=intimacy_value,
                intimacy_gt=intimacy_gt,
                min_turns=min_turns,
                filter_lang=filter_lang,
                gender=gender,
                orientation=orientation,
                recommend_debug=_waterfall_debug,
            )
            if recommend_debug is not None:
                recommend_debug.update(_waterfall_debug)

            # 兜底策略：所有策略未命中时按全站亲密度总和降序（最受欢迎）
            use_popularity = _waterfall_debug.get("popularity_fallback", False)

            from services.recommend import strategy_rank_order_clause

            order_cols = []
            rank_clause = strategy_rank_order_clause(
                CompanionORM.tags,
                _waterfall_debug.get("_pool"),
                _waterfall_debug.get("_tag_def_map") or {},
            )
            if rank_clause is not None:
                order_cols.append(rank_clause.asc())

            if is_created is not True:
                prefer = _lang_prefer_clause(lang, match)
                if prefer is not None:
                    order_cols.append(prefer)

            if use_popularity:
                # 子查询：全站亲密度总和
                pop_subq = (
                    db.query(
                        UserCompanionStateORM.companion_id,
                        func.sum(UserCompanionStateORM.affection).label("pop_affection"),
                    )
                    .group_by(UserCompanionStateORM.companion_id)
                    .subquery()
                )
                query = query.outerjoin(pop_subq, pop_subq.c.companion_id == CompanionORM.id)
                order_cols.append(func.coalesce(pop_subq.c.pop_affection, 0).desc())
            else:
                order_cols.append(func.coalesce(UserCompanionStateORM.affection, 0).desc())

            query = query.order_by(*order_cols)

            _waterfall_debug.pop("_pool", None)
            _waterfall_debug.pop("_tag_def_map", None)
            if recommend_debug is not None:
                recommend_debug.pop("_pool", None)
                recommend_debug.pop("_tag_def_map", None)

            # 分页
            total = query.count()
            rows = query.offset(offset).limit(limit).all()

            # 查询最后一条消息（复用同一 db session，省一次连接）
            companion_ids = [row.id for row, _, _, _ in rows]
            last_msgs = _batch_last_messages_with_db(db, companion_ids, user_id=user_id, lang=lang) if companion_ids else {}
            # 展示轮数/亲密度只读 user_companion_states，与详情 to_dict 同源，不回退全局 companion_states
            user_states = _batch_user_companion_states_with_db(db, user_id, companion_ids) if companion_ids else {}

            # 构造结果
            items = []
            for row, user_affection, user_turns, comp_summary in rows:
                last = last_msgs.get(row.id)
                us = user_states.get(row.id)
                aff = float(us["affection"]) if us is not None else (float(user_affection) if user_affection is not None else 0.0)
                turns = int(us["turns"]) if us is not None else (int(user_turns) if user_turns is not None else 0)
                item = {
                    "profile": {
                        "id": row.id,
                        "name": row.name or "",
                        "age": row.age or 0,
                        "gender": stored_gender(row.gender, default=""),
                        "city": row.city or "",
                        "personality": row.personality or "",
                        "background": row.background or "",
                        "description": getattr(row, "description", None) or row.personality or "",
                        "speech_style": row.speech_style or "",
                        "appearance": getattr(row, "appearance", None) or "",
                        "hobbies": row.hobbies or "",
                        "values": row.values or "",
                        "fears": row.fears or "",
                        "love_view": row.love_view or "",
                        "daily_routine": row.daily_routine or "",
                        "favorite_things": row.favorite_things or "",
                        "mbti": row.mbti or "",
                        "sexual_orientation": row.sexual_orientation or "",
                        "life_story": row.life_story or "",
                        "cultural_values": row.cultural_values or "",
                        "gender_perspective": row.gender_perspective or "",
                        "avatar_url": row.avatar_url or "",
                        "created_by": row.created_by or "",
                        "language": row.language or "zh",
                        "tier": getattr(row, "tier", None) or "featured",
                        "is_deleted": row.is_deleted or False,
                        "created_at": row.created_at.isoformat() if row.created_at else "",
                    },
                    "state": {
                        "affection": aff,
                        "summary": comp_summary or "",
                        "turns": turns,
                    },
                    "avatar": row.avatar_url or "",
                    "avatar_generating": row.avatar_url == "__GENERATING__",
                    "last_message": last["content"] if last else "",
                    "last_message_time": last["timestamp"] if last else "",
                }
                items.append(item)

            return items, total

    def _list_recommend_scene(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
        lang: str = "zh",
        match: str = "prefer",
        filter_lang: str = "",
        gender: str = "",
        orientation: str = "",
        recommend_debug: Optional[dict] = None,
    ) -> tuple:
        """推荐场景：先套 companions_recommend 策略；命中为空或不够一页时热门补全。
        补全按全站亲密度总和 + 聊天持续时长，且不与策略结果重复。
        """
        from core.database import get_db, CompanionORM, UserCompanionStateORM, CompanionStateORM
        from sqlalchemy import case, literal_column
        from services.companion_report import list_excluded_companion_ids_for_user

        with get_db() as db:
            owner_keys = self._owner_keys(user_id, db=db)

            excluded_ids = list_excluded_companion_ids_for_user(db, user_id)
            # 子查询：每个 companion 的全站亲密度总和
            affection_subq = (
                db.query(
                    UserCompanionStateORM.companion_id,
                    func.sum(UserCompanionStateORM.affection).label("total_affection"),
                )
                .group_by(UserCompanionStateORM.companion_id)
                .subquery()
            )

            # 子查询：每个 companion 的第一条消息时间
            first_msg_subq = (
                db.query(
                    ShortTermMessageORM.companion_id,
                    func.min(ShortTermMessageORM.created_at).label("first_msg_at"),
                )
                .group_by(ShortTermMessageORM.companion_id)
                .subquery()
            )

            # 子查询：每个 companion 的最后一条消息时间
            last_msg_subq = (
                db.query(
                    ShortTermMessageORM.companion_id,
                    func.max(ShortTermMessageORM.created_at).label("last_msg_at"),
                )
                .group_by(ShortTermMessageORM.companion_id)
                .subquery()
            )

            # 聊天持续时长（秒）：无消息时为 0
            duration_sec = func.coalesce(
                func.extract("epoch", last_msg_subq.c.last_msg_at - first_msg_subq.c.first_msg_at),
                0,
            ).label("chat_duration_sec")

            query = (
                db.query(
                    CompanionORM,
                    func.coalesce(affection_subq.c.total_affection, 0).label("total_affection"),
                    duration_sec,
                    CompanionStateORM.summary.label("comp_summary"),
                )
                .outerjoin(affection_subq, affection_subq.c.companion_id == CompanionORM.id)
                .outerjoin(first_msg_subq, first_msg_subq.c.companion_id == CompanionORM.id)
                .outerjoin(last_msg_subq, last_msg_subq.c.companion_id == CompanionORM.id)
                .outerjoin(CompanionStateORM, CompanionStateORM.companion_id == CompanionORM.id)
                .filter(
                    self.soft_delete_visibility_clause(CompanionORM, owner_keys),
                    ~CompanionORM.id.in_(excluded_ids) if excluded_ids else True,
                )
            )
            prefer = _lang_prefer_clause(lang, match)
            pop_order = [
                func.coalesce(affection_subq.c.total_affection, 0).desc(),
                duration_sec.desc(),
            ]
            _wf_debug = recommend_debug if recommend_debug is not None else {}
            from services.recommend import (
                apply_hard_filters,
                resolve_companion_list_lang,
                should_pad_recommend_pool,
                strategy_rank_order_clause,
                window_for_strategy_then_fill,
            )
            strategy_query = self._apply_list_waterfall(
                db,
                query,
                user_id=user_id,
                lang=lang,
                special="recommend",
                filter_lang=filter_lang,
                gender=gender,
                orientation=orientation,
                recommend_debug=_wf_debug,
            )
            order_cols = []
            rank_clause = strategy_rank_order_clause(
                CompanionORM.tags,
                _wf_debug.get("_pool"),
                _wf_debug.get("_tag_def_map") or {},
            )
            if rank_clause is not None:
                order_cols.append(rank_clause.asc())
            if prefer is not None:
                order_cols.append(prefer)
            order_cols.extend(pop_order)
            strategy_query = strategy_query.order_by(*order_cols)

            _wf_debug.pop("_pool", None)
            _wf_debug.pop("_tag_def_map", None)

            strategy_total = int(strategy_query.count() or 0)
            strategy_matched = _wf_debug.get("matched_strategy_id") is not None
            if not should_pad_recommend_pool(
                strategy_total, limit, strategy_matched=strategy_matched,
            ):
                total = strategy_total
                rows = strategy_query.offset(offset).limit(limit).all()
            else:
                strategy_rows = strategy_query.all() if strategy_total else []
                strategy_ids = [row.id for row, *_ in strategy_rows]
                fill_query = apply_hard_filters(
                    query,
                    CompanionORM,
                    resolve_companion_list_lang(
                        db, user_id, filter_lang=filter_lang, accept_lang=lang
                    ),
                    gender,
                    orientation,
                )
                if strategy_ids:
                    fill_query = fill_query.filter(~CompanionORM.id.in_(strategy_ids))
                fill_order = ([prefer] if prefer is not None else []) + pop_order
                fill_query = fill_query.order_by(*fill_order)
                fill_total = int(fill_query.count() or 0)
                s_off, s_lim, f_off, f_lim, total = window_for_strategy_then_fill(
                    len(strategy_rows), fill_total, offset, limit,
                )
                rows = list(strategy_rows[s_off:s_off + s_lim]) if s_lim else []
                if f_lim:
                    rows.extend(fill_query.offset(f_off).limit(f_lim).all())
                _wf_debug["popularity_fallback"] = True
                if (_wf_debug.get("fallback_layer") or "") in ("", "empty"):
                    _wf_debug["fallback_layer"] = "popularity_fill"

            # 批量查最后一条消息（复用同一 db session）
            companion_ids = [row.id for row, _, _, _ in rows]
            last_msgs = _batch_last_messages_with_db(db, companion_ids, user_id=user_id, lang=lang) if companion_ids else {}

            # 查当前用户的亲密度（复用同一 db session）
            user_states = _batch_user_companion_states_with_db(db, user_id, companion_ids) if companion_ids else {}

            items = []
            for row, total_affection, chat_duration_sec, comp_summary in rows:
                last = last_msgs.get(row.id)
                us = user_states.get(row.id, {})
                duration_hours = round(float(chat_duration_sec or 0) / 3600, 1)
                item = {
                    "profile": {
                        "id": row.id,
                        "name": row.name or "",
                        "age": row.age or 0,
                        "gender": stored_gender(row.gender, default=""),
                        "city": row.city or "",
                        "personality": row.personality or "",
                        "background": row.background or "",
                        "description": getattr(row, "description", None) or row.personality or "",
                        "speech_style": row.speech_style or "",
                        "appearance": getattr(row, "appearance", None) or "",
                        "hobbies": row.hobbies or "",
                        "values": row.values or "",
                        "fears": row.fears or "",
                        "love_view": row.love_view or "",
                        "daily_routine": row.daily_routine or "",
                        "favorite_things": row.favorite_things or "",
                        "mbti": row.mbti or "",
                        "sexual_orientation": row.sexual_orientation or "",
                        "life_story": row.life_story or "",
                        "cultural_values": row.cultural_values or "",
                        "gender_perspective": row.gender_perspective or "",
                        "avatar_url": row.avatar_url or "",
                        "created_by": row.created_by or "",
                        "language": row.language or "zh",
                        "tier": getattr(row, "tier", None) or "featured",
                        "is_deleted": row.is_deleted or False,
                        "created_at": row.created_at.isoformat() if row.created_at else "",
                    },
                    "state": {
                        "affection": float(us.get("affection") or 0),
                        "summary": comp_summary or "",
                        "turns": us.get("turns", 0),
                    },
                    "avatar": row.avatar_url or "",
                    "avatar_generating": row.avatar_url == "__GENERATING__",
                    "last_message": last["content"] if last else "",
                    "last_message_time": last["timestamp"] if last else "",
                    "total_affection": float(total_affection or 0),
                    "chat_duration_hours": float(duration_hours),
                }
                items.append(item)

            return items, total

    def _list_message_scene(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
        lang: str = "zh",
        sort: Optional[str] = None,
    ) -> tuple:
        """消息场景：有剩余聊天记录才进列表（清空记录后不再出现）。
        入池：自己创建的 / 亲密度>=0.01 / 与当前用户有过消息；再要求 PG 里仍有消息。
        默认按 last_message_time 近→远；sort=affection 按亲密度倒序，并额外纳入自己创建的（无聊天也返回，排在前面）。
        不套推荐策略（apply_to 的 recommend / all / moments 都不影响本列表）。
        """
        from core.database import get_db, CompanionORM, UserCompanionStateORM, CompanionStateORM, ShortTermMessageORM
        from sqlalchemy import and_, or_
        from services.companion_report import list_excluded_companion_ids_for_user

        sort_key = _normalize_message_list_sort(sort)

        with get_db() as db:
            owner_keys = self._owner_keys(user_id, db=db)
            owner_key_list = list(owner_keys)

            excluded_ids = list_excluded_companion_ids_for_user(db, user_id)

            # 有过任意消息（含「用户已发、AI 尚未回复」）的 companion
            messaged_subq = (
                db.query(ShortTermMessageORM.companion_id)
                .filter(ShortTermMessageORM.user_id == user_id)
                .distinct()
                .subquery()
            )

            last_msg_subq = (
                db.query(
                    ShortTermMessageORM.companion_id.label("companion_id"),
                    func.max(ShortTermMessageORM.created_at).label("last_msg_at"),
                )
                .filter(ShortTermMessageORM.user_id == user_id)
                .group_by(ShortTermMessageORM.companion_id)
                .subquery()
            )

            owned_clause = CompanionORM.created_by.in_(owner_key_list) if owner_key_list else False
            # clear 后亲密度可能仍为 0（block/dislike 清零过）：靠「有消息 / 有过对话轮次」入池
            interacted_clause = or_(
                UserCompanionStateORM.affection >= 0.01,
                func.coalesce(UserCompanionStateORM.turns, 0) >= 1,
                CompanionORM.id.in_(db.query(messaged_subq.c.companion_id)),
            )
            chatted_clause = and_(
                last_msg_subq.c.last_msg_at.isnot(None),
                interacted_clause,
            )

            # 使用 LEFT JOIN 一次性查出 companions + user_companion_states + companion_states
            query = db.query(
                CompanionORM,
                func.coalesce(UserCompanionStateORM.affection, 0).label("user_affection"),
                func.coalesce(UserCompanionStateORM.turns, 0).label("user_turns"),
                CompanionStateORM.summary.label("comp_summary"),
            ).outerjoin(
                UserCompanionStateORM,
                (UserCompanionStateORM.companion_id == CompanionORM.id) &
                (UserCompanionStateORM.user_id == user_id)
            ).outerjoin(
                CompanionStateORM,
                CompanionStateORM.companion_id == CompanionORM.id
            ).outerjoin(
                last_msg_subq,
                last_msg_subq.c.companion_id == CompanionORM.id,
            ).filter(
                self.soft_delete_visibility_clause(CompanionORM, owner_keys),
                ~CompanionORM.id.in_(excluded_ids) if excluded_ids else True,
            )

            if sort_key == "affection":
                # 首页：自己创建的即使没聊过也要返回；其余仍须有聊天记录
                query = query.filter(or_(owned_clause, chatted_clause) if owner_key_list else chatted_clause)
            else:
                pool = (
                    or_(owned_clause, interacted_clause)
                    if owner_key_list
                    else interacted_clause
                )
                query = query.filter(
                    pool,
                    last_msg_subq.c.last_msg_at.isnot(None),
                )

            last_msg_order = nulls_last(desc(last_msg_subq.c.last_msg_at))
            created_order = desc(CompanionORM.created_at)
            if sort_key == "affection":
                order_cols = []
                if owner_key_list:
                    order_cols.append(desc(case((owned_clause, 1), else_=0)))
                order_cols.extend(
                    [
                        desc(func.coalesce(UserCompanionStateORM.affection, 0)),
                        last_msg_order,
                        created_order,
                    ]
                )
                query = query.order_by(*order_cols)
            else:
                query = query.order_by(last_msg_order, created_order)

            total = query.count()
            rows = query.offset(offset).limit(limit).all()

            # 查询最后一条消息（复用同一 db session）
            companion_ids = [row.id for row, _, _, _ in rows]
            last_msgs = _batch_last_messages_with_db(db, companion_ids, user_id=user_id, lang=lang) if companion_ids else {}
            user_states = _batch_user_companion_states_with_db(db, user_id, companion_ids) if companion_ids else {}

            # 构造结果
            items = []
            for row, user_affection, user_turns, comp_summary in rows:
                last = last_msgs.get(row.id)
                us = user_states.get(row.id)
                aff = float(us["affection"]) if us is not None else (float(user_affection) if user_affection is not None else 0.0)
                turns = int(us["turns"]) if us is not None else (int(user_turns) if user_turns is not None else 0)
                item = {
                    "profile": {
                        "id": row.id,
                        "name": row.name or "",
                        "age": row.age or 0,
                        "gender": stored_gender(row.gender, default=""),
                        "city": row.city or "",
                        "personality": row.personality or "",
                        "background": row.background or "",
                        "description": getattr(row, "description", None) or row.personality or "",
                        "speech_style": row.speech_style or "",
                        "appearance": getattr(row, "appearance", None) or "",
                        "hobbies": row.hobbies or "",
                        "values": row.values or "",
                        "fears": row.fears or "",
                        "love_view": row.love_view or "",
                        "daily_routine": row.daily_routine or "",
                        "favorite_things": row.favorite_things or "",
                        "mbti": row.mbti or "",
                        "sexual_orientation": row.sexual_orientation or "",
                        "life_story": row.life_story or "",
                        "cultural_values": row.cultural_values or "",
                        "gender_perspective": row.gender_perspective or "",
                        "avatar_url": row.avatar_url or "",
                        "created_by": row.created_by or "",
                        "language": row.language or "zh",
                        "tier": getattr(row, "tier", None) or "featured",
                        "is_deleted": row.is_deleted or False,
                        "created_at": row.created_at.isoformat() if row.created_at else "",
                    },
                    "state": {
                        "affection": aff,
                        "summary": comp_summary or "",
                        "turns": turns,
                    },
                    "avatar": row.avatar_url or "",
                    "avatar_generating": row.avatar_url == "__GENERATING__",
                    "last_message": last["content"] if last else "",
                    "last_message_time": last["timestamp"] if last else "",
                }
                is_owned = (row.created_by or "").strip() in owner_keys
                # 消息 Tab：无预览不进列表。首页 sort=affection：自己创建的即使没聊过也保留
                if not (item["last_message"] or "").strip():
                    if sort_key != "affection" or not is_owned:
                        continue
                items.append(item)

            # Redis 热房间可能比 PG 更新：默认排序按展示用 last_message_time 再排当前页
            if sort_key != "affection":
                items.sort(key=lambda x: x.get("last_message_time") or "", reverse=True)

            return items, total
