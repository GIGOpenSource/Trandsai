import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from api.auth import verify_token

router = APIRouter(tags=["管理后台"])
logger = logging.getLogger(__name__)


# ===== 请求/响应模型 =====

class AdminLoginRequest(BaseModel):
    username: Optional[str] = Field(None, description="管理员用户名（默认 gigadmin）", examples=["gigadmin"])
    password: str = Field(..., description="管理员密码", examples=["your_password"])

class AdminLoginResponse(BaseModel):
    token: str = Field(..., description="认证 Token，后续请求通过 x-token 或 Authorization Bearer 传递")

class ErrorResponse(BaseModel):
    error: str = Field(..., description="错误信息")
    status_code: int = Field(..., description="HTTP 状态码")


# ===== 后端错误信息多语言 =====
_ADMIN_ERROR_MESSAGES = {
    "zh": {
        "missing_auth": "缺少认证信息",
        "invalid_token": "Token 无效或已过期",
        "wrong_password": "密码错误",
        "account_inactive": "账号未生效或已暂停，无法登录",
        "companion_not_found": "智能体不存在",
        "companion_scope_forbidden": "无权管理该智能体",
        "entry_not_found": "条目不存在",
        "moment_not_found": "朋友圈不存在",
        "moment_no_caption": "朋友圈不存在或无文案",
        "config_group_not_found": "配置组不存在",
        "key_name_required": "key 和 name 不能为空",
        "invalid_config_type": "config_type 必须是 model_service、agent、image_generation、system 或 payment",
        "key_exists": "key 已存在",
        "feedback_not_found": "反馈会话不存在",
        "user_not_found": "用户不存在",
        "notification_not_found": "通知不存在",
        "batch_generate_failed": "批量生成失败",
        "companion_id_caption_required": "companion_id 和 caption 为必填项",
        "empty_content": "回复内容不能为空",
        "username_too_short": "用户名至少3个字符",
        "password_too_short": "密码至少6个字符",
        "password_old_wrong": "原密码错误",
        "password_change_unsupported": "当前登录方式不支持修改密码，请使用管理员账号登录",
        "username_exists": "用户名已存在",
        "prompt_required": "prompt 为必填项",
        "image_generation_failed": "图片生成失败",
        "invalid_aspect_ratio": "aspect_ratio 不合法，可选：1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 3:2 / 2:3 / 4:5 / 5:4 / 21:9",
        "invalid_image_size": "image_size 不合法，可选：1K / 2K / 4K",
        "title_content_required": "标题和内容不能为空",
        "unsupported_language": "不支持的语言",
        "server_error": "服务器内部错误",
        "reset_confirm_required": "请确认清空全部数据，confirm 必须为 yes_clear_all_data",
        "invalid_affection": "亲密度必须在 0～100 之间",
        "affection_required": "请填写亲密度",
        "session_not_found": "未找到该用户与智能体的会话记录",
        "im_ops_not_admin": "指定的责任运营必须是后台管理员账号",
        "im_assign_ops_forbidden": "仅超级管理员可指定责任运营",
        "im_session_forbidden": "无权查看或操作该会话",
        "im_takeover_forbidden": "仅责任运营或超级管理员可接管该会话",
        "im_already_taken_over": "该会话已被其他运营接管",
        "im_admin_id_required": "当前登录态缺少管理员 ID，无法接管",
        "im_release_forbidden": "仅当前接管人或超级管理员可释放接管",
        "im_not_taken_over": "会话未处于接管状态，无法发送运营消息",
        "im_send_forbidden": "仅当前接管人或超级管理员可发送消息",
        "im_invalid_reply_mode": "回复模式须为 human_first（优先人工）或 ai（AI回复）",
        "review_not_found": "审核记录不存在",
        "review_not_pending": "该记录不在待审核状态",
        "invalid_reject_reason": "驳回原因无效，请选择预设原因",
        "post_not_found": "帖子不存在",
        "report_not_found": "举报记录不存在",
        "report_not_pending": "该举报不在待审核状态",
        "not_found": "记录不存在",
        "comment_not_found": "评论不存在",
        "content_required": "消息文案内容不能为空",
        "companion_ids_required": "请选择作用智能体",
        "application_not_found": "未找到该智能体策略",
        "companion_id_immutable": "编辑时不能更换作用智能体，请新建一条",
    },
    "en": {
        "missing_auth": "Missing authentication",
        "invalid_token": "Invalid or expired token",
        "wrong_password": "Incorrect password",
        "account_inactive": "Account is inactive or suspended",
        "companion_not_found": "Companion not found",
        "companion_scope_forbidden": "No permission for this companion",
        "entry_not_found": "Entry not found",
        "moment_not_found": "Moment not found",
        "moment_no_caption": "Moment not found or has no caption",
        "config_group_not_found": "Config group not found",
        "key_name_required": "key and name are required",
        "invalid_config_type": "config_type must be model_service, agent, image_generation, system or payment",
        "key_exists": "key already exists",
        "feedback_not_found": "Feedback thread not found",
        "user_not_found": "User not found",
        "notification_not_found": "Notification not found",
        "batch_generate_failed": "Batch generation failed",
        "companion_id_caption_required": "companion_id and caption are required",
        "empty_content": "Reply content cannot be empty",
        "username_too_short": "Username must be at least 3 characters",
        "password_too_short": "Password must be at least 6 characters",
        "password_old_wrong": "Incorrect original password",
        "password_change_unsupported": "Current login cannot change password; use an admin account",
        "username_exists": "Username already exists",
        "prompt_required": "prompt is required",
        "image_generation_failed": "Image generation failed",
        "invalid_aspect_ratio": "invalid aspect_ratio; use 1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 3:2 / 2:3 / 4:5 / 5:4 / 21:9",
        "invalid_image_size": "invalid image_size; use 1K / 2K / 4K",
        "title_content_required": "Title and content are required",
        "unsupported_language": "Unsupported language",
        "server_error": "Internal server error",
        "reset_confirm_required": "Please confirm to clear all data, confirm must be yes_clear_all_data",
        "invalid_affection": "Affection must be between 0 and 100",
        "affection_required": "Affection is required",
        "session_not_found": "No chat session for this user and companion",
        "im_ops_not_admin": "ops_admin_id must be an admin account",
        "im_assign_ops_forbidden": "Only super admin can assign responsible ops",
        "im_session_forbidden": "No permission for this session",
        "im_takeover_forbidden": "Only the responsible ops or super admin can take over",
        "im_already_taken_over": "Session is already taken over by another operator",
        "im_admin_id_required": "Admin user id missing from token; cannot take over",
        "im_release_forbidden": "Only the current takeover operator or super admin can release",
        "im_not_taken_over": "Session is not under takeover",
        "im_send_forbidden": "Only the current takeover operator or super admin can send",
        "im_invalid_reply_mode": "reply_mode must be human_first or ai",
        "review_not_found": "Review record not found",
        "review_not_pending": "Review is not pending",
        "invalid_reject_reason": "Invalid reject reason_id",
        "post_not_found": "Post not found",
        "report_not_found": "Report not found",
        "report_not_pending": "Report is not pending",
        "not_found": "Not found",
        "comment_not_found": "Comment not found",
        "content_required": "Message content is required",
        "companion_ids_required": "Please select companion(s)",
        "application_not_found": "Strategy application not found",
        "companion_id_immutable": "Companion cannot be changed on edit; create a new row",
    },
}


def _get_error_msg(key: str, lang: str = "zh") -> str:
    """根据语言获取错误信息"""
    lang = lang.split("-")[0]  # zh-CN -> zh
    return _ADMIN_ERROR_MESSAGES.get(lang, _ADMIN_ERROR_MESSAGES["zh"]).get(key, key)


# 本地化字典映射
_LOCALIZE_MAP = {
    "gender": {
        "zh": {"male": "男", "female": "女"},
        "en": {"male": "Male", "female": "Female"},
    },
    "sexual_orientation": {
        "zh": {"heterosexual": "异性恋", "homosexual": "同性恋", "bisexual": "双性恋"},
        "en": {"heterosexual": "Heterosexual", "homosexual": "Homosexual", "bisexual": "Bisexual"},
    },
}


def _localize_dict(data: dict, fields: list, lang: str = "zh") -> dict:
    """本地化字典中的指定字段"""
    result = data.copy()
    lang = lang.split("-")[0]
    for field, field_type in fields:
        if field in result and result[field]:
            value = result[field]
            localize_map = _LOCALIZE_MAP.get(field_type, {}).get(lang, {})
            if value in localize_map:
                result[field] = localize_map[value]
    return result


async def get_admin_lang(accept_language: Optional[str] = Header(None, alias="Accept-Language")) -> str:
    """从请求头解析管理员界面语言，默认中文"""
    if accept_language:
        lang = accept_language.split(",")[0].strip().lower()
        lang = lang.split("-")[0]
        if lang in _ADMIN_ERROR_MESSAGES:
            return lang
    return "zh"


security = HTTPBearer(auto_error=False)


def _extract_admin_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
    x_token: Optional[str],
) -> str:
    token = (x_token or "").strip()
    if not token and credentials is not None:
        token = (credentials.credentials or "").strip()
    if not token:
        auth = (request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif auth:
            token = auth
    return token


async def admin_auth_required(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """管理端鉴权：优先 x-token，其次 Authorization Bearer。"""
    token = _extract_admin_token(request, credentials, x_token)
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token

