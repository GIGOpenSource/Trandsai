"""Admin LLM/image/agent config-group routes. Registers on admin_common.router."""
import os
from typing import Optional

from dotenv import set_key
from fastapi import Body, Depends, HTTPException

from api.admin_common import (
    _get_error_msg,
    admin_auth_required,
    get_admin_lang,
    router,
)
from api.auth import clear_all_admin_tokens
from core.config import BASE_DIR
from core.i18n import _
from core.database import AgentConfigORM, CompanionAgentConfigORM, ConfigGroupORM, get_db
from services.agent import test_llm_connection

_COST_CONFIG_KEYS = frozenset({"system_costs", "free_turns_limit", "vip_plans"})
_AFFECTION_FLOOR_KEY = "default_affection_floor"
_SAY_HI_DAILY_KEY = "say_hi_daily_free_limit"
_MOMENT_COMMENT_LIMIT_KEY = "moment_comment_limit"
_ALLOWED_CONFIG_TYPES = frozenset(
    {"model_service", "agent", "image_generation", "system", "payment"}
)


def _maybe_refresh_cost_config(row: ConfigGroupORM) -> None:
    if not row:
        return
    key = (row.key or "").strip()
    if row.config_type == "system" or key in _COST_CONFIG_KEYS:
        from services.credit_service import refresh_cost_config_cache

        refresh_cost_config_cache()
    if key == _AFFECTION_FLOOR_KEY:
        from services.affection_floor import refresh_affection_floor_cache

        refresh_affection_floor_cache()
    if key == _SAY_HI_DAILY_KEY:
        from services.say_hi_quota import refresh_say_hi_daily_limit_cache

        refresh_say_hi_daily_limit_cache()
    if key == _MOMENT_COMMENT_LIMIT_KEY:
        from services.moment_comment_limit import refresh_moment_comment_limit_cache

        refresh_moment_comment_limit_cache()


def _normalize_config_json_for_key(key: str, config_json) -> dict:
    data = dict(config_json or {}) if isinstance(config_json, dict) else {}
    k = (key or "").strip()
    if k == _AFFECTION_FLOOR_KEY:
        from services.affection_floor import normalize_floor_config_json

        return normalize_floor_config_json(data)
    if k == _SAY_HI_DAILY_KEY:
        from services.say_hi_quota import normalize_say_hi_daily_config_json

        return normalize_say_hi_daily_config_json(data)
    if k == _MOMENT_COMMENT_LIMIT_KEY:
        from services.moment_comment_limit import normalize_moment_comment_limit_config_json

        return normalize_moment_comment_limit_config_json(data)
    if k == "free_turns_limit":
        from services.credit_service import _parse_cost_value

        raw = data.get("free_turns_limit", 3)
        data["free_turns_limit"] = _parse_cost_value("free_turns_limit", raw)
        return data
    return data

# ===== 配置组管理 =====

# 前端新建/编辑页字段布局（按 key；同 type 不同 key 字段不同）
_FORM_SCHEMAS: dict = {
    "free_turns_limit": {
        "config_type": "system",
        "title": "聊天次数",
        "description": "每个智能体可免费聊天的次数（与 Say Hi 每日免费分开；按用户×智能体计数）",
        "layout": "single_number",
        "fields": [
            {
                "path": "free_turns_limit",
                "label": "每智能体免费次数",
                "type": "number",
                "min": 0,
                "max": 9999,
                "default": 3,
                "required": True,
            }
        ],
    },
    "say_hi_daily_free_limit": {
        "config_type": "system",
        "title": "Say Hi 每日免费次数",
        "description": "每位用户每天可免费 Say Hi 的次数（UTC 日切）；用尽后需用户确认才会扣聊天免费次数",
        "layout": "single_number",
        "fields": [
            {
                "path": "say_hi_daily_free_limit",
                "label": "每日免费 Say Hi 次数",
                "type": "number",
                "min": 0,
                "max": 9999,
                "default": 10,
                "required": True,
            }
        ],
    },
    "moment_comment_limit": {
        "config_type": "system",
        "title": "朋友圈评论次数上限",
        "description": "同一用户对同一条朋友圈可成功发送的评论条数；智能体回复不计；达上限返回业务码 460 引导私聊",
        "layout": "single_number",
        "fields": [
            {
                "path": "moment_comment_limit",
                "label": "每用户每动态评论上限",
                "type": "number",
                "min": 0,
                "max": 9999,
                "default": 1,
                "required": True,
            }
        ],
    },
    "default_affection_floor": {
        "config_type": "system",
        "title": "默认亲密档位",
        "description": "提示词用亲密托底（广告户）；不改库内真实亲密度",
        "layout": "single_number",
        "fields": [
            {
                "path": "default_affection_floor",
                "label": "默认档位",
                "type": "number",
                "min": 20,
                "max": 100,
                "default": 80,
                "required": True,
            }
        ],
    },
    "system_costs": {
        "config_type": "system",
        "title": "系统扣费",
        "description": "综合扣费规则（可选；与专用免费次数配置并存时以后者为准）",
        "layout": "cost_grid",
        "fields": [
            {"path": "chat_cost", "label": "聊天扣积分", "type": "number", "min": 0, "default": 3, "required": True},
            {"path": "create_agent_cost", "label": "创建智能体扣积分", "type": "number", "min": 0, "default": 50, "required": True},
            {"path": "agent_daily_cost", "label": "智能体日扣积分", "type": "number", "min": 0, "default": 10, "required": True},
            {"path": "free_turns_limit", "label": "每智能体免费次数", "type": "number", "min": 0, "max": 9999, "default": 3, "required": False},
        ],
    },
    "vip_plans": {
        "config_type": "payment",
        "title": "VIP与积分配置",
        "description": "VIP 档位与充值包；扣费/免费次数请改 system 专用配置组",
        "layout": "vip_plans",
        "fields": [
            {
                "path": "plans",
                "label": "VIP 档位",
                "type": "object_map",
                "required": True,
                "item_fields": [
                    {"path": "daily_credits", "label": "每日赠送积分", "type": "number", "min": 0, "required": True},
                    {"path": "max_agents", "label": "可持有智能体数", "type": "number", "min": 0, "required": True},
                    {"path": "duration_days", "label": "天数", "type": "number", "min": 1, "required": True},
                ],
                "preset_keys": ["weekly", "monthly", "yearly", "lifetime"],
            },
            {
                "path": "recharge_packages",
                "label": "充值包",
                "type": "object_map",
                "required": True,
                "item_fields": [
                    {"path": "credits", "label": "积分数", "type": "number", "min": 1, "required": True},
                ],
                "preset_keys": ["pack_250", "pack_550", "pack_800", "pack_1900"],
            },
        ],
        "ignore_legacy_paths": ["chat_cost", "create_agent_cost", "agent_daily_cost", "free_turns_limit"],
    },
    "agent": {
        "config_type": "agent",
        "title": "Agent 配置",
        "description": "对话模型与系统提示词",
        "layout": "agent",
        "fields": [
            {
                "path": "model_provider",
                "label": "模型提供商",
                "type": "select",
                "options": ["", "google", "deepseek", "xai"],
                "default": "",
                "required": False,
            },
            {"path": "temperature", "label": "Temperature", "type": "number", "min": 0, "max": 2, "step": 0.01, "default": 0.93, "required": True},
            {"path": "max_tokens", "label": "Max Tokens", "type": "number", "min": 1, "max": 8192, "default": 2048, "required": True},
            {"path": "system_prompt_zh", "label": "系统提示词 zh", "type": "textarea", "required": False},
            {"path": "system_prompt_en", "label": "系统提示词 en", "type": "textarea", "required": False},
            {"path": "system_prompt_ja", "label": "系统提示词 ja", "type": "textarea", "required": False},
            {"path": "system_prompt_ko", "label": "系统提示词 ko", "type": "textarea", "required": False},
            {"path": "system_prompt_pt", "label": "系统提示词 pt", "type": "textarea", "required": False},
            {"path": "system_prompt_es", "label": "系统提示词 es", "type": "textarea", "required": False},
            {"path": "system_prompt_id", "label": "系统提示词 id", "type": "textarea", "required": False},
            {"path": "system_prompt_ar", "label": "系统提示词 ar", "type": "textarea", "required": False},
            {"path": "system_prompt_fa", "label": "系统提示词 fa", "type": "textarea", "required": False},
            {"path": "system_prompt_ru", "label": "系统提示词 ru", "type": "textarea", "required": False},
            {"path": "system_prompt_bg", "label": "系统提示词 bg", "type": "textarea", "required": False},
            {"path": "system_prompt_tr", "label": "系统提示词 tr", "type": "textarea", "required": False},
        ],
    },
    "model_service": {
        "config_type": "model_service",
        "title": "模型服务配置",
        "description": "默认模型提供商与就绪状态（密钥也可走环境变量）",
        "layout": "model_service",
        "fields": [
            {
                "path": "model_provider",
                "label": "默认提供商",
                "type": "select",
                "options": ["google", "deepseek", "xai"],
                "default": "google",
                "required": True,
            },
            {"path": "google_ready", "label": "Google 已配置", "type": "boolean", "readonly": True, "required": False},
            {"path": "deepseek_ready", "label": "DeepSeek 已配置", "type": "boolean", "readonly": True, "required": False},
            {"path": "xai_ready", "label": "xAI 已配置", "type": "boolean", "readonly": True, "required": False},
            {"path": "admin_password_set", "label": "管理员密码已设置", "type": "boolean", "readonly": True, "required": False},
            {"path": "google_key", "label": "Google API Key（可选覆盖）", "type": "password", "required": False},
            {"path": "deepseek_key", "label": "DeepSeek API Key（可选覆盖）", "type": "password", "required": False},
            {"path": "xai_key", "label": "xAI API Key（可选覆盖）", "type": "password", "required": False},
        ],
    },
    "image_generation": {
        "config_type": "image_generation",
        "title": "图片生成配置",
        "description": "默认文生图链路",
        "layout": "image_generation",
        "fields": [
            {
                "path": "provider",
                "label": "Provider",
                "type": "select",
                "options": ["google", "gemini", "xai", "grok"],
                "default": "google",
                "required": True,
            },
            {"path": "api_key", "label": "API Key", "type": "password", "required": False},
            {"path": "model", "label": "模型", "type": "text", "default": "gemini-2.5-flash-image", "required": True},
            {"path": "base_url", "label": "Base URL", "type": "text", "required": False},
            {"path": "timeout", "label": "超时秒数", "type": "number", "min": 10, "max": 600, "default": 120, "required": False},
            {"path": "default_width", "label": "默认宽", "type": "number", "min": 256, "max": 2048, "default": 1024, "required": False},
            {"path": "default_height", "label": "默认高", "type": "number", "min": 256, "max": 2048, "default": 1024, "required": False},
        ],
    },
}

# image_gemini / image_xai 与 image_generation 同布局
_FORM_SCHEMAS["image_gemini"] = {
    **_FORM_SCHEMAS["image_generation"],
    "title": "Gemini 文生图配置",
}
_FORM_SCHEMAS["image_xai"] = {
    **_FORM_SCHEMAS["image_generation"],
    "title": "xAI Grok 绘图配置",
}


@router.get("/api/admin/config-groups/options", summary="配置组筛选项与表单布局")
async def admin_config_group_options(_token: str = Depends(admin_auth_required)):
    """前端新建/编辑页：按 key 取 fields 渲染；未知 key 用 config_type 兜底 JSON 编辑。"""
    return {
        "config_types": sorted(_ALLOWED_CONFIG_TYPES),
        "form_by_key": _FORM_SCHEMAS,
        "create_meta_fields": [
            {"path": "key", "label": "Key", "type": "text", "required": True, "edit_locked": True},
            {"path": "name", "label": "名称", "type": "text", "required": True},
            {"path": "description", "label": "描述", "type": "textarea", "required": False},
            {
                "path": "config_type",
                "label": "类型",
                "type": "select",
                "options": sorted(_ALLOWED_CONFIG_TYPES),
                "required": True,
                "edit_locked": True,
            },
            {"path": "enabled", "label": "启用", "type": "boolean", "default": True, "required": True},
            {"path": "sort_order", "label": "排序", "type": "number", "default": 0, "required": False},
        ],
        "notes": [
            "编辑页锁定 key / config_type，不可改。",
            "保存时提交整份 config_json；用响应 config_json 回填。",
            "优先用 form_by_key[item.key]；没有则按 config_type 或 JSON 编辑器。",
        ],
    }


@router.get("/api/admin/config-groups", summary="获取配置组列表")
async def admin_list_config_groups(_token: str = Depends(admin_auth_required)):
    with get_db() as db:
        rows = db.query(ConfigGroupORM).order_by(ConfigGroupORM.sort_order.asc(), ConfigGroupORM.id.asc()).all()
        return [
            {
                "id": r.id,
                "key": r.key,
                "name": r.name,
                "description": r.description,
                "config_type": r.config_type,
                "config_json": r.config_json or {},
                "enabled": bool(r.enabled),
                "sort_order": r.sort_order,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


@router.get("/api/admin/config-groups/{group_id}", summary="获取配置组详情")
async def admin_get_config_group(group_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        row = db.query(ConfigGroupORM).filter(ConfigGroupORM.id == group_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("config_group_not_found", lang))
        return {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "description": row.description,
            "config_type": row.config_type,
            "config_json": row.config_json or {},
            "enabled": bool(row.enabled),
            "sort_order": row.sort_order,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


@router.post("/api/admin/config-groups", summary="创建配置组")
async def admin_create_config_group(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    key = data.get("key", "").strip()
    name = data.get("name", "").strip()
    config_type = data.get("config_type", "agent").strip()
    if not key or not name:
        raise HTTPException(status_code=400, detail=_get_error_msg("key_name_required", lang))
    if config_type not in _ALLOWED_CONFIG_TYPES:
        raise HTTPException(status_code=400, detail=_get_error_msg("invalid_config_type", lang))
    with get_db() as db:
        exists = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == key).first()
        if exists:
            raise HTTPException(status_code=400, detail=_get_error_msg("key_exists", lang))
        default_config = {}
        if config_type == "model_service":
            default_config = {
                "model_provider": "google",
                "google_ready": False,
                "deepseek_ready": False,
                "xai_ready": False,
                "admin_password_set": False,
            }
        elif config_type == "agent":
            default_config = {
                "model_provider": "",
                "temperature": 0.93,
                "max_tokens": 2048,
                "system_prompt_zh": "",
                "system_prompt_en": "",
                "system_prompt_ja": "",
                "system_prompt_ko": "",
                "system_prompt_pt": "",
                "system_prompt_es": "",
                "system_prompt_id": "",
                "system_prompt_ar": "",
                "system_prompt_fa": "",
                "system_prompt_ru": "",
                "system_prompt_bg": "",
                "system_prompt_tr": "",
            }
        elif config_type == "image_generation":
            default_config = {
                "provider": "google",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "api_key": "",
                "model": "gemini-2.5-flash-image",
                "default_width": 1024,
                "default_height": 1024,
            }
        elif config_type == "payment":
            default_config = {
                "plans": {},
                "recharge_packages": {},
            }
        elif config_type == "system":
            from services.credit_service import _DEFAULT_COSTS

            if key == "free_turns_limit":
                default_config = {"free_turns_limit": _DEFAULT_COSTS["free_turns_limit"]}
            elif key == "system_costs":
                default_config = dict(_DEFAULT_COSTS)
            elif key == "default_affection_floor":
                from services.affection_floor import FLOOR_DEFAULT, normalize_floor_config_json

                default_config = normalize_floor_config_json(
                    {"default_affection_floor": FLOOR_DEFAULT}
                )
            elif key == "say_hi_daily_free_limit":
                from services.say_hi_quota import (
                    LIMIT_DEFAULT,
                    normalize_say_hi_daily_config_json,
                )

                default_config = normalize_say_hi_daily_config_json(
                    {"say_hi_daily_free_limit": LIMIT_DEFAULT}
                )
            else:
                default_config = dict(data.get("config_json") or {})
        group = ConfigGroupORM(
            key=key,
            name=name,
            description=data.get("description", ""),
            config_type=config_type,
            config_json=_normalize_config_json_for_key(
                key, {**default_config, **(data.get("config_json") or {})}
            ),
            enabled=1 if data.get("enabled", True) else 0,
            sort_order=data.get("sort_order", 0),
        )
        db.add(group)
        db.flush()
        db.commit()
        _maybe_refresh_cost_config(group)
        return {
            "id": group.id,
            "key": group.key,
            "name": group.name,
            "config_type": group.config_type,
            "config_json": group.config_json or {},
            "enabled": bool(group.enabled),
            "sort_order": group.sort_order,
        }


@router.put("/api/admin/config-groups/{group_id}", summary="更新配置组")
async def admin_update_config_group(group_id: int, data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        row = db.query(ConfigGroupORM).filter(ConfigGroupORM.id == group_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("config_group_not_found", lang))
        if "enabled" in data:
            row.enabled = 1 if data["enabled"] else 0
        if "name" in data:
            row.name = data["name"]
        if "description" in data:
            row.description = data["description"]
        if "sort_order" in data:
            row.sort_order = data["sort_order"]
        if "config_json" in data:
            row.config_json = _normalize_config_json_for_key(row.key or "", data["config_json"])
        db.commit()
        _maybe_refresh_cost_config(row)
        return {
            "ok": True,
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "description": row.description,
            "config_type": row.config_type,
            "config_json": row.config_json or {},
            "enabled": bool(row.enabled),
            "sort_order": row.sort_order,
        }


@router.delete("/api/admin/config-groups/{group_id}", summary="删除配置组")
async def admin_delete_config_group(group_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        row = db.query(ConfigGroupORM).filter(ConfigGroupORM.id == group_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("config_group_not_found", lang))
        db.delete(row)
    return {"ok": True}


@router.get("/api/admin/config", summary="获取系统配置")
async def admin_config(_token: str = Depends(admin_auth_required)):
    return {
        "google_ready": bool(os.getenv("GOOGLE_API_KEY", "") or os.getenv("GOOGLEAI_API_KEY", "")),
        "deepseek_ready": bool(os.getenv("DEEPSEEK_API_KEY", "")),
        "xai_ready": bool(os.getenv("XAI_API_KEY", "")),
        "model_provider": os.getenv("MODEL_PROVIDER", "google"),
        "admin_password_set": bool(os.getenv("ADMIN_PASSWORD", "")),
    }


@router.post("/api/admin/config", summary="更新系统配置")
async def admin_update_config(data: dict, _token: str = Depends(admin_auth_required)):
    dotenv_path = str(BASE_DIR / "server" / ".env")
    updated = []

    field_map = {
        "google_key": "GOOGLE_API_KEY",
        "deepseek_key": "DEEPSEEK_API_KEY",
        "xai_key": "XAI_API_KEY",
        "admin_password": "ADMIN_PASSWORD",
    }

    for key, env_name in field_map.items():
        value = data.get(key)
        if value is not None:
            set_key(dotenv_path, env_name, value)
            os.environ[env_name] = value
            updated.append(env_name)

    provider = data.get("model_provider")
    if provider is not None:
        set_key(dotenv_path, "MODEL_PROVIDER", provider)
        os.environ["MODEL_PROVIDER"] = provider
        updated.append("MODEL_PROVIDER")

    if "ADMIN_PASSWORD" in updated:
        clear_all_admin_tokens()

    return {"ok": True, "updated": updated}


@router.post("/api/admin/config/test", summary="测试模型连接")
async def admin_test_config(data: dict, _token: str = Depends(admin_auth_required)):
    provider = data.get("provider") or os.getenv("MODEL_PROVIDER", "google")
    result = test_llm_connection(provider=provider)
    return result


@router.post("/api/admin/config-groups/{group_id}/test", summary="测试配置组连接")
async def admin_test_config_group(
    group_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """根据配置组类型执行连通性测试"""
    with get_db() as db:
        row = db.query(ConfigGroupORM).filter(ConfigGroupORM.id == group_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("config_group_not_found", lang))

        cfg = row.config_json or {}
        config_type = row.config_type

        if config_type == "model_service":
            data = data or {}
            provider = data.get("provider") or cfg.get("model_provider") or os.getenv("MODEL_PROVIDER", "google")
            cfg_eff = dict(cfg)
            for k in ("google_key", "deepseek_key", "xai_key"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    cfg_eff[k] = v.strip()
            overrides = {}
            for k in ("google_key", "deepseek_key", "xai_key"):
                v = cfg_eff.get(k)
                if isinstance(v, str) and v.strip():
                    overrides[k] = v.strip()
            result = test_llm_connection(provider=provider, api_key_overrides=overrides if overrides else None)
            return result

        if config_type == "image_generation":
            provider = (cfg.get("provider") or "").lower()
            if not provider:
                return {"ok": False, "error": _("Provider 未配置")}
            if provider in ("google", "gemini"):
                try:
                    from services.image_generation import generate_image_with_gemini
                    raw = generate_image_with_gemini(
                        "A simple red circle on white background, minimal",
                        width=512,
                        height=512,
                        config=cfg,
                    )
                    if raw:
                        return {"ok": True, "response": _("Gemini 文生图连接正常")}
                    return {"ok": False, "error": _("Gemini 文生图无返回")}
                except Exception as e:
                    return {"ok": False, "error": str(e)}
            if provider in ("grok", "xai"):
                try:
                    from services.image_generation import generate_image_with_xai
                    url = generate_image_with_xai(
                        "A simple red circle on white background, minimal",
                        width=512,
                        height=512,
                        config=cfg,
                    )
                    if url:
                        return {"ok": True, "response": _("xAI 文生图连接正常")}
                    return {"ok": False, "error": _("xAI 文生图无返回")}
                except Exception as e:
                    return {"ok": False, "error": str(e)}
            return {"ok": False, "error": _("不支持的图片 provider（已移除 volcano）")}

        if config_type == "agent":
            override_provider = cfg.get("model_provider", "")
            if override_provider:
                result = test_llm_connection(provider=override_provider)
                return result
            temperature = cfg.get("temperature")
            max_tokens = cfg.get("max_tokens")
            if temperature is None or max_tokens is None:
                return {"ok": False, "error": _("Temperature 或 Max Tokens 未配置")}
            return {"ok": True, "response": _("配置完整")}

        if config_type == "system":
            return {"ok": True, "response": _("系统配置无需连通性测试")}

        return {"ok": False, "error": f"{_('未知配置类型')}: {config_type}"}


@router.get("/api/admin/agent-config", summary="获取Agent配置")
async def admin_get_agent_config(_token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(AgentConfigORM).first()
        if row:
            return row.config_json or {}
    return {}


@router.put("/api/admin/agent-config", summary="更新Agent配置")
async def admin_put_agent_config(data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(AgentConfigORM).first()
        if row:
            cfg = dict(row.config_json or {})
            cfg.update(data)
            row.config_json = cfg
        else:
            db.add(AgentConfigORM(config_json=data))
    return {"ok": True}


@router.get("/api/admin/companions/{companion_id}/agent-config", summary="获取伴侣Agent配置")
async def admin_get_companion_agent_config(
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)
    with get_db() as db:
        row = db.query(CompanionAgentConfigORM).filter(
            CompanionAgentConfigORM.companion_id == companion_id
        ).first()
        if row and row.config_json:
            return row.config_json
    return {}


@router.put("/api/admin/companions/{companion_id}/agent-config", summary="更新伴侣Agent配置")
async def admin_put_companion_agent_config(
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)
    with get_db() as db:
        row = db.query(CompanionAgentConfigORM).filter(
            CompanionAgentConfigORM.companion_id == companion_id
        ).first()
        if row:
            cfg = dict(row.config_json or {})
            cfg.update(data)
            row.config_json = cfg
        else:
            db.add(CompanionAgentConfigORM(companion_id=companion_id, config_json=data))
    try:
        from services.agent_utils import invalidate_agent_config_cache
        from services.companion_cache_bridge import (
            ACTION_AGENT_CONFIG,
            publish_companion_invalidate,
        )

        invalidate_agent_config_cache(companion_id)
        publish_companion_invalidate(companion_id, action=ACTION_AGENT_CONFIG)
    except Exception:
        pass
    return {"ok": True}

