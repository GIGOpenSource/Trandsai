import os
import re
from pathlib import Path

# 项目路径
BASE_DIR = Path(__file__).parent.parent.parent
MEMORY_ROOT = str(BASE_DIR / "data" / "memory")

# 客户端静态文件目录
CLIENT_DIR = BASE_DIR / "client"
ADMIN_DIR = BASE_DIR / "admin" / "dist"
DIST_DIR = BASE_DIR / "client" / "dist"

# 模型常不用 \\n，而用「句末标点 + 连续空白」分段；只认真换行会整段进一条气泡
# 不含 …：避免「……  」等被误切成多条
_PSEUDO_LINE_BREAK = re.compile(
    r"(?<=[。！？.!?])(?:[ \t\u00a0\u3000]{2,})"
)


def _expand_pseudo_linebreaks(s: str) -> str:
    """把句末标点后的多段空白视作换行，便于与真实 \\n 一并拆分。"""
    return _PSEUDO_LINE_BREAK.sub("\n", s)


def split_response(text: str, max_chars: int = 150) -> list[str]:
    """将长文本按换行、空行（含 Unicode 行/段分隔）与句子拆分为多条消息，模拟真人分条发送"""
    if not text:
        return []

    text = _expand_pseudo_linebreaks(text)

    # 换行、回车、Unicode 行分隔 U+2028 / 段分隔 U+2029；连续空段忽略，每段非空内容单独成条
    lines = [p.strip() for p in re.split(r"[\r\n\u2028\u2029]+", text) if p.strip()]

    if not lines:
        return []

    # 仅一行且很短，直接返回
    if len(lines) == 1 and len(lines[0]) <= max_chars:
        return [lines[0]]

    all_segments = []
    for para in lines:
        if len(para) <= max_chars:
            all_segments.append(para)
            continue

        # 对长段落按句子拆分
        raw_parts = re.split(r'([。！？.!?]+)', para)
        segments = []
        current = ""
        i = 0
        while i < len(raw_parts):
            part = raw_parts[i]
            if i + 1 < len(raw_parts):
                part += raw_parts[i + 1]
                i += 2
            else:
                i += 1

            if not part.strip():
                continue

            if len(current) + len(part) <= max_chars:
                current += part
            else:
                if current.strip():
                    segments.append(current.strip())
                current = part

        if current.strip():
            segments.append(current.strip())

        result = []
        for seg in segments:
            while len(seg) > int(max_chars * 1.3):
                split_at = max_chars
                while split_at < len(seg) and seg[split_at] not in '，, ':
                    split_at += 1
                if split_at >= len(seg) or split_at <= max_chars // 2:
                    split_at = max_chars
                result.append(seg[:split_at].strip())
                seg = seg[split_at:].lstrip('，, ')
            if seg:
                result.append(seg)
        all_segments.extend(result)

    return all_segments if all_segments else [text]


from core.i18n import (
    _AGENT_TIMEOUT_MESSAGE,
    _CONNECT_MESSAGES,
    _DUPLICATE_USER_MESSAGE,
    _LEAVE_INTENT_KEYWORDS,
    _MULTI_MESSAGE_AGENT_PREFIX,
    _QUEUE_COALESCED_MESSAGE,
)


def detect_leave_intent(text: str) -> bool:
    """检测用户输入是否包含离开意图"""
    lower = text.lower()
    for kw in _LEAVE_INTENT_KEYWORDS:
        if kw.lower() in lower:
            return True
    return False
