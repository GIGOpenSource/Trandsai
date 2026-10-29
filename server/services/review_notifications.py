"""审核通过 / 驳回后推送到用户系统通知列表。"""
from __future__ import annotations

from typing import Any, Optional

from core.database import SystemNotificationORM

_SUPPORTED_LANGS = ("zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr")

_PROFILE_APPROVED = {
    "zh": ("资料审核通过", "你的个人资料修改已通过审核，现已对他人展示。"),
    "en": ("Profile approved", "Your profile update has been approved and is now visible to others."),
    "ja": ("プロフィール審査通過", "プロフィールの変更が承認され、他のユーザーに表示されます。"),
    "ko": ("프로필 심사 통과", "프로필 수정이 승인되어 다른 사용자에게 표시됩니다."),
    "pt": ("Perfil aprovado", "A atualização do seu perfil foi aprovada e já está visível para outros."),
    "es": ("Perfil aprobado", "Tu cambio de perfil fue aprobado y ya es visible para otros."),
    "id": ("Profil disetujui", "Perubahan profil Anda telah disetujui dan kini terlihat oleh orang lain."),
}

_POST_APPROVED = {
    "zh": ("帖子审核通过", "你的帖子「{title}」已通过审核，现已对他人展示。"),
    "en": ("Post approved", "Your post “{title}” has been approved and is now visible to others."),
    "ja": ("投稿審査通過", "投稿「{title}」が承認され、他のユーザーに表示されます。"),
    "ko": ("게시물 심사 통과", "게시물 「{title}」이(가) 승인되어 다른 사용자에게 표시됩니다."),
    "pt": ("Publicação aprovada", "Sua publicação “{title}” foi aprovada e já está visível para outros."),
    "es": ("Publicación aprobada", "Tu publicación “{title}” fue aprobada y ya es visible para otros."),
    "id": ("Postingan disetujui", "Postingan “{title}” Anda telah disetujui dan kini terlihat oleh orang lain."),
}

_PROFILE_REJECTED = {
    "zh": ("资料审核未通过", "你的个人资料修改未通过审核。{reason}"),
    "en": ("Profile rejected", "Your profile update was not approved. {reason}"),
    "ja": ("プロフィール審査否決", "プロフィールの変更は承認されませんでした。{reason}"),
    "ko": ("프로필 심사 반려", "프로필 수정이 승인되지 않았습니다. {reason}"),
    "pt": ("Perfil rejeitado", "A atualização do seu perfil não foi aprovada. {reason}"),
    "es": ("Perfil rechazado", "Tu cambio de perfil no fue aprobado. {reason}"),
    "id": ("Profil ditolak", "Perubahan profil Anda tidak disetujui. {reason}"),
}


def _clip_title(title: str, max_len: int = 40) -> str:
    t = (title or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def push_review_approved_notifications(
    db: Any,
    user_id: Optional[int],
    kind: str,
    *,
    post_title: str = "",
) -> int:
    """
    写入系统通知（按语言各一条，定向到 user_id）。
    kind: profile | post
    返回创建条数。
    """
    if not user_id:
        return 0

    if kind == "profile":
        templates = _PROFILE_APPROVED
        fmt: dict = {}
    elif kind == "post":
        templates = _POST_APPROVED
        fmt = {"title": _clip_title(post_title) or "-"}
    else:
        return 0

    count = 0
    for lang in _SUPPORTED_LANGS:
        title, content = templates.get(lang, templates["en"])
        try:
            content = content.format(**fmt) if fmt else content
        except Exception:
            pass
        db.add(
            SystemNotificationORM(
                title=title[:200],
                content=content,
                language=lang,
                user_id=int(user_id),
            )
        )
        count += 1

    try:
        from services.push_sender import send_push_to_user

        t_zh, c_zh = templates.get("zh", templates["en"])
        try:
            c_zh = c_zh.format(**fmt) if fmt else c_zh
        except Exception:
            pass
        send_push_to_user(
            int(user_id),
            title=t_zh[:200],
            body=c_zh[:200],
            data={"type": f"review_{kind}"},
        )
    except Exception:
        pass
    return count


def push_review_rejected_notifications(
    db: Any,
    user_id: Optional[int],
    kind: str,
    *,
    reason: str = "",
    post_title: str = "",
) -> int:
    """驳回通知；kind: profile | post。"""
    if not user_id:
        return 0
    reason_text = (reason or "").strip()
    if kind == "profile":
        templates = _PROFILE_REJECTED
        fmt = {"reason": f"原因：{reason_text}" if reason_text else ""}
    elif kind == "post":
        templates = {
            "zh": ("帖子审核未通过", "你的帖子「{title}」未通过审核。{reason}"),
            "en": ("Post rejected", "Your post “{title}” was not approved. {reason}"),
        }
        for lang in _SUPPORTED_LANGS:
            if lang not in templates:
                templates[lang] = templates["en"]
        fmt = {
            "title": _clip_title(post_title) or "-",
            "reason": f"Reason: {reason_text}" if reason_text else "",
        }
    else:
        return 0

    count = 0
    for lang in _SUPPORTED_LANGS:
        title, content = templates.get(lang, templates.get("en") or next(iter(templates.values())))
        try:
            content = content.format(**fmt).strip()
        except Exception:
            pass
        db.add(
            SystemNotificationORM(
                title=title[:200],
                content=content,
                language=lang,
                user_id=int(user_id),
            )
        )
        count += 1
    try:
        from services.push_sender import send_push_to_user

        t_zh, c_zh = templates.get("zh", templates.get("en", ("", "")))
        try:
            c_zh = c_zh.format(**fmt).strip()
        except Exception:
            pass
        send_push_to_user(
            int(user_id),
            title=t_zh[:200],
            body=c_zh[:200],
            data={"type": f"review_{kind}_rejected"},
        )
    except Exception:
        pass
    return count
