"""成文记忆切片：与 prepare 独立，仅 stdlib，便于单测。

原则：只做「结构预算」——近聊优先、头区按剩余字数压缩。
不在这里用词表判断「要不要往事」；memory 里已有的块（含 episodes）
能装进预算就留，装不下再从头部截断。语义相关由上游拼装决定。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

RESPOND_MEMORY_CHARS_DEFAULT = 3200
RESPOND_DIALOGUE_CHARS_DEFAULT = 2400
RESPOND_RECENT_MESSAGES_DEFAULT = 14

RECENT_DIALOGUE_MARKERS = (
    "<<RECENT_DIALOGUE>>",
    "【最近对话】",
    "[Recent chat]",
)


def respond_memory_slice_on() -> bool:
    return os.getenv("RESPOND_MEMORY_SLICE", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _is_dialogue_header_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    if s.startswith("<<") or s.startswith("【"):
        return True
    if s.lower().startswith("[recent"):
        return True
    return False


def trim_respond_dialogue(
    dialogue: str, *, max_chars: int, max_msgs: int
) -> Tuple[str, int]:
    """近聊：保留标记行 + 从尾部往前最多 max_msgs 条，且总长 ≤ max_chars。"""
    raw = (dialogue or "").strip()
    if not raw:
        return "", 0
    lines = raw.split("\n")
    headers: List[str] = []
    body: List[str] = []
    for i, line in enumerate(lines):
        if not body and _is_dialogue_header_line(line):
            headers.append(line)
            continue
        body = lines[i:]
        break
    if not body and headers:
        joined = "\n".join(headers).strip()
        return joined[:max_chars], 0

    msg_indices = [j for j, ln in enumerate(body) if ln.strip()]
    if len(msg_indices) > max_msgs:
        keep_from = msg_indices[-max_msgs]
        body = body[keep_from:]

    header_text = "\n".join(headers).strip()
    kept_body: List[str] = []
    size = len(header_text)
    if header_text:
        size += 1
    for line in reversed(body):
        add = len(line) + (1 if kept_body or header_text else 0)
        if size + add > max_chars:
            break
        kept_body.insert(0, line)
        size += add
    if not kept_body and body:
        for line in reversed(body):
            if line.strip():
                room = max(0, max_chars - len(header_text) - 1)
                kept_body = [line[:room]]
                break

    parts = [header_text] if header_text else []
    parts.extend(kept_body)
    out = "\n".join(p for p in parts if p is not None).strip()
    msg_count = sum(1 for ln in kept_body if ln.strip())
    return out, msg_count


def slice_memory_for_respond(
    memory_text: str,
    user_input: str = "",
    *,
    max_chars: Optional[int] = None,
    dialogue_chars: Optional[int] = None,
    recent_messages: Optional[int] = None,
) -> str:
    """成文专用记忆切片：近聊原文优先，头区（卡/事实/往事）按剩余预算压缩。

    与 prepare 切片独立。不按用户措辞删块——上游已写入的内容一视同仁，
    只受字数/条数预算约束。user_input 保留签名兼容，当前不参与判定。
    """
    del user_input  # 不用词表/句面做语义门控
    text = (memory_text or "").strip()
    if not text:
        return ""

    total_cap = (
        max_chars
        if max_chars is not None
        else _env_positive_int("RESPOND_MEMORY_CHARS", RESPOND_MEMORY_CHARS_DEFAULT)
    )
    dlg_cap = (
        dialogue_chars
        if dialogue_chars is not None
        else _env_positive_int(
            "RESPOND_DIALOGUE_CHARS", RESPOND_DIALOGUE_CHARS_DEFAULT
        )
    )
    msg_cap = (
        recent_messages
        if recent_messages is not None
        else _env_positive_int(
            "RESPOND_RECENT_MESSAGES", RESPOND_RECENT_MESSAGES_DEFAULT
        )
    )
    dlg_cap = min(dlg_cap, total_cap)

    idx = -1
    for marker in RECENT_DIALOGUE_MARKERS:
        i = text.find(marker)
        if i >= 0 and (idx < 0 or i < idx):
            idx = i

    if idx < 0:
        out = text if len(text) <= total_cap else text[-total_cap:]
        if len(text) != len(out):
            logger.info(
                "respond_memory_slice no_marker %s->%s",
                len(text),
                len(out),
            )
        return out

    dialogue_raw = text[idx:]
    head = text[:idx].strip()

    dialogue, msg_n = trim_respond_dialogue(
        dialogue_raw, max_chars=dlg_cap, max_msgs=msg_cap
    )
    head_budget = total_cap - len(dialogue) - (2 if dialogue and head else 0)
    if head and head_budget > 80:
        if len(head) > head_budget:
            # 保尾：事实/往事常靠对话侧更密
            head = "…" + head[-head_budget + 1 :]
        out = f"{head}\n\n{dialogue}".strip() if dialogue else head
    else:
        out = dialogue or (head[:total_cap] if head else "")

    if len(out) > total_cap:
        out = out[-total_cap:]

    if len(text) != len(out):
        head_kept = 0
        if head and dialogue and out.endswith(dialogue):
            head_kept = max(0, len(out) - len(dialogue) - 2)
        elif head and not dialogue:
            head_kept = len(out)
        logger.info(
            "respond_memory_slice %s->%s dlg_msgs=%s dlg_chars=%s head_chars=%s",
            len(text),
            len(out),
            msg_n,
            len(dialogue),
            head_kept,
        )
    return out
