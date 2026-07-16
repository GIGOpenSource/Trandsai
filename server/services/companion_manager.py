import os
import shutil
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from core.database import (
    CompanionORM,
    CompanionStateORM,
    UserCompanionStateORM,
    CompanionAgentConfigORM,
    FactORM,
    MomentORM,
    MomentCommentORM,
    MomentLikeORM,
    RelationSummaryORM,
    ShortTermMessageORM,
    get_db, UserORM,
)
from services.image_generation import generate_avatar_prompt, generate_image
from services.memory import CompanionMemory
from services.culture_data import infer_language_from_city

logger = logging.getLogger(__name__)


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
            companion.state.affection = ur.affection if ur.affection is not None else 0
            companion.state.turns = ur.turns if ur.turns is not None else 0
            return
        gr = (
            db.query(CompanionStateORM)
            .filter(CompanionStateORM.companion_id == companion.profile.id)
            .first()
        )
        if gr:
            companion.state.affection = gr.affection if gr.affection is not None else 0
            companion.state.turns = gr.turns if gr.turns is not None else 0


class CompanionProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = Field(..., min_length=1, max_length=20)
    age: int = Field(..., ge=18, le=35)
    gender: str = Field(default="女", pattern=r"^(男|女)$")
    city: str = Field(..., min_length=1, max_length=20)
    personality: str = Field(..., min_length=5, max_length=500)
    background: str = Field(..., min_length=5, max_length=1000)
    speech_style: str = Field(..., min_length=5, max_length=500)
    hobbies: str = Field(default="", max_length=300)
    values: str = Field(default="", max_length=300)
    fears: str = Field(default="", max_length=300)
    love_view: str = Field(default="", max_length=300)
    daily_routine: str = Field(default="", max_length=500)
    favorite_things: str = Field(default="", max_length=300)
    mbti: str = Field(default="", max_length=10)
    sexual_orientation: str = Field(default="", max_length=20)
    life_story: str = Field(default="", max_length=2000)
    cultural_values: str = Field(default="", max_length=1500)
    gender_perspective: str = Field(default="", max_length=1000)
    avatar_url: str = Field(default="", max_length=500)
    created_by: str = Field(default="", max_length=64)
    language: str = Field(default="zh", max_length=10)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def clamp_companion_profile_dict(data: dict) -> dict:
    """按 CompanionProfile 各字段的 MaxLen 截断字符串，避免 LLM/导入数据超长导致校验失败。"""
    out = dict(data)
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


class Companion:
    def __init__(self, profile: CompanionProfile, memory_root: str, state: Optional[CompanionState] = None):
        self.profile = profile
        self.dir = os.path.join(memory_root, profile.id)
        os.makedirs(self.dir, exist_ok=True)
        self.memory = CompanionMemory(profile.id, self.dir)
        self.state = state if state is not None else self._load_state()

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

    def to_dict(self, user_id: Optional[int] = None) -> dict:
        avatar = self.profile.avatar_url
        is_generating = avatar == "__GENERATING__"
        if not avatar or is_generating:
            avatar = f"https://api.dicebear.com/7.x/avataaars/svg?seed={self.profile.id}"
        state_payload = self.state.model_dump()
        if user_id is not None:
            with get_db() as db:
                ur = (
                    db.query(UserCompanionStateORM)
                    .filter(
                        UserCompanionStateORM.user_id == user_id,
                        UserCompanionStateORM.companion_id == self.profile.id,
                    )
                    .first()
                )
                if ur:
                    state_payload["affection"] = ur.affection if ur.affection is not None else 0
                    state_payload["turns"] = ur.turns if ur.turns is not None else 0
                else:
                    gr = (
                        db.query(CompanionStateORM)
                        .filter(CompanionStateORM.companion_id == self.profile.id)
                        .first()
                    )
                    if gr:
                        state_payload["affection"] = gr.affection if gr.affection is not None else 0
                        state_payload["turns"] = gr.turns if gr.turns is not None else 0
        return {
            "profile": self.profile.model_dump(),
            "state": state_payload,
            "avatar": avatar,
            "avatar_generating": is_generating,
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
                        profile = CompanionProfile(
                            id=row.id,
                            name=row.name,
                            age=row.age if row.age is not None else 18,
                            gender=row.gender or "女",
                            city=row.city or "未知",
                            personality=row.personality or "温柔体贴",
                            background=row.background or "",
                            speech_style=row.speech_style or "",
                            hobbies=row.hobbies or "",
                            values=row.values or "",
                            fears=row.fears or "",
                            love_view=row.love_view or "",
                            daily_routine=row.daily_routine or "",
                            favorite_things=row.favorite_things or "",
                            mbti=row.mbti or "",
                            sexual_orientation=row.sexual_orientation or "",
                            life_story=row.life_story or "",
                            cultural_values=row.cultural_values or "",
                            gender_perspective=row.gender_perspective or "",
                            avatar_url=row.avatar_url or "",
                            created_by=row.created_by or "",
                            language=row.language or "zh",
                            created_at=row.created_at.isoformat() if row.created_at else datetime.now(timezone.utc).isoformat(),
                        )
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
        profile = CompanionProfile(**clamp_companion_profile_dict(profile_data))

        # 强制确保 language 与地区（city）信息一致
        if not getattr(profile, 'language', None) or profile.language == "zh":
            profile.language = infer_language_from_city(profile.city)
        elif profile.language != infer_language_from_city(profile.city):
            # 如果不一致，强制修改为地区匹配的语言
            inferred = infer_language_from_city(profile.city)
            logger.info(
                "[CompanionManager] Forcing language for %s from %s to %s based on city %s",
                profile.name,
                profile.language,
                inferred,
                profile.city,
            )
            profile.language = inferred

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
                return self._companions.get(existing.id) or Companion(
                    CompanionProfile(
                        id=existing.id,
                        name=existing.name,
                        age=existing.age or 18,
                        gender=existing.gender or "女",
                        city=existing.city or "未知",
                        personality=existing.personality or "温柔体贴",
                        background=existing.background or "",
                        speech_style=existing.speech_style or "",
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
                        created_by=existing.created_by or "",
                        language=existing.language or "zh",
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
                speech_style=profile.speech_style,
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
                created_by=profile.created_by or "",
                language=profile.language or "zh",
                created_at=datetime.fromisoformat(profile.created_at) if profile.created_at else datetime.now(timezone.utc),
            ))
            db.add(CompanionStateORM(companion_id=profile.id))

        companion = Companion(profile, self.memory_root)
        self._companions[profile.id] = companion

        # 导入聊天记录
        if chat_history:
            for msg in chat_history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and content:
                    companion.memory.add_user_message(content)
                elif role == "assistant" and content:
                    companion.memory.add_assistant_message(content)

        # 标记头像待生成，由调用方启动后台异步任务
        if not profile.avatar_url:
            profile.avatar_url = "__GENERATING__"
            with get_db() as db:
                row = db.query(CompanionORM).filter(CompanionORM.id == profile.id).first()
                if row:
                    row.avatar_url = "__GENERATING__"

        return companion

    def get(self, companion_id: str) -> Optional[Companion]:
        return self._companions.get(companion_id)


    def list_all(self, user_id: Optional[int] = None) -> List[Dict]:
        """获取 companions 列表。
        必须提供 user_id，只返回该用户拥有的 companions（created_by 匹配）。
        """
        if user_id is None:
            return []

        result = []
        user_id_str = str(user_id)

        # 获取用户信息用于 username/nickname 匹配
        username = ""
        nickname = ""
        with get_db() as db:
            user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                username = (user.username or "").strip()
                nickname = (user.nickname or "").strip()

        for c in self._companions.values():
            created_by = (c.profile.created_by or "").strip()

            # 必须匹配：created_by == user_id 或 username 或 nickname
            if created_by != user_id_str and created_by != username and created_by != nickname:
                continue  # 不属于该用户，跳过

            item = c.to_dict(user_id=user_id)
            recent = c.memory.short_term.get_recent(1)
            if recent:
                last = recent[-1]
                item["last_message"] = last["content"]
                item["last_message_time"] = last["timestamp"]
            else:
                item["last_message"] = ""
                item["last_message_time"] = ""
            result.append(item)
        return result

    def list_all_for_any(self, filter_type: str = "all") -> List[Dict]:
        """获取所有 companions 列表（不过滤用户）

        Args:
            filter_type: 过滤类型
                - "all": 返回所有智能体（默认）
                - "chatted": 返回有对话的智能体（turns > 0）
                - "affectionate": 返回有亲密度的智能体（affection > 0）
        """
        result = []
        for c in self._companions.values():
            # 根据 filter_type 过滤
            if filter_type == "chatted":
                # 只返回有对话的智能体
                if not c.state or c.state.turns <= 0:
                    continue
            elif filter_type == "affectionate":
                # 只返回有亲密度的智能体
                if not c.state or c.state.affection <= 0:
                    continue

            item = c.to_dict()
            recent = c.memory.short_term.get_recent(1)
            if recent:
                last = recent[-1]
                item["last_message"] = last["content"]
                item["last_message_time"] = last["timestamp"]
            else:
                item["last_message"] = ""
                item["last_message_time"] = ""
            result.append(item)
        return result

    def update(self, companion_id: str, data: dict) -> Optional[Companion]:
        companion = self._companions.get(companion_id)
        if not companion:
            return None

        # 更新内存中的 profile（添加 mbti 支持）
        updatable = {"name", "age", "gender", "city", "personality", "background", "speech_style", "hobbies", "values", "fears", "love_view", "daily_routine", "favorite_things", "mbti", "sexual_orientation", "life_story", "cultural_values", "gender_perspective", "avatar_url", "created_by", "language"}
        for key in updatable:
            if key in data:
                setattr(companion.profile, key, data[key])

        # 如果更新了 city 或 language，确保一致性
        if "city" in data or "language" in data:
            city = getattr(companion.profile, 'city', '')
            inferred = infer_language_from_city(city)
            if getattr(companion.profile, 'language', 'zh') != inferred:
                companion.profile.language = inferred
                logger.info(
                    "[CompanionManager] Updated language for %s to %s based on city %s",
                    companion.profile.name,
                    inferred,
                    city,
                )

        # 同步更新 MySQL
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if row:
                for key in updatable:
                    if key in data:
                        setattr(row, key, data[key])

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

        return companion

    def delete(self, companion_id: str) -> bool:
        companion = self._companions.pop(companion_id, None)
        if companion:
            with get_db() as db:
                # 先删除关联的 moment 点赞和评论
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
                db.query(CompanionORM).filter(
                    CompanionORM.id == companion_id
                ).delete(synchronize_session=False)
            shutil.rmtree(companion.dir, ignore_errors=True)
            return True
        return False

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
