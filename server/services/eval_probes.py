"""D5：对话质量自动探针（轻量，无独立评测平台）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def probe_cross_talk(context_a: str, token_b: str) -> bool:
    """串聊探针：B 的上下文是否泄漏 A 的独特 token。True=事故。"""
    if not token_b:
        return False
    return token_b in (context_a or "")


def probe_leave_heavy(light_path: bool, has_leave_intent: bool) -> bool:
    """离开样本应走重路径。返回 True 表示命中正确。"""
    if not has_leave_intent:
        return True
    return not light_path


def probe_summary_due_updated(summary_due: bool, old_summary: str, new_summary: str) -> bool:
    if not summary_due:
        return True
    return (new_summary or "").strip() != (old_summary or "").strip()


def probe_near_duplicate(a: str, b: str, threshold: float = 0.85) -> bool:
    """简易字符级相似；True=过相似。"""
    sa = re.sub(r"\s+", "", (a or ""))
    sb = re.sub(r"\s+", "", (b or ""))
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    # Jaccard on bigrams
    def bigrams(s: str):
        return {s[i : i + 2] for i in range(max(0, len(s) - 1))}

    ba, bb = bigrams(sa), bigrams(sb)
    if not ba or not bb:
        return False
    inter = len(ba & bb)
    union = len(ba | bb)
    return (inter / union) >= threshold


def _longest_run(a: str, b: str, need: int = 16) -> int:
    """两段去空白文本的最长公共连续片段。够长即返回，避免扫完整矩阵。"""
    if not a or not b:
        return 0
    a, b = a[:800], b[:800]
    best = 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        row_best = 0
        for j, cb in enumerate(b):
            if ca == cb:
                v = prev[j] + 1
                if v > row_best:
                    row_best = v
            else:
                v = 0
            cur.append(v)
        prev = cur
        if row_best > best:
            best = row_best
            if best >= need:
                return best
    return best


def probe_leitmotif_repeat(
    reply: str,
    recent: List[str],
    *,
    ngram: int = 5,
    min_hits: int = 3,
    lookback: int = 8,
) -> bool:
    """检测「换皮复读」：本轮与近几轮共享连续片段，不必整段几乎相同。

    Jaccard 对同主题换说法常 <0.4 漏检；用最长公共片段 + n-gram 重叠补洞。
    """
    sa = re.sub(r"\s+", "", (reply or ""))
    if len(sa) < ngram:
        return False
    recent_ngrams = set()
    for prev in (recent or [])[-lookback:]:
        sb = re.sub(r"\s+", "", (prev or ""))
        if len(sb) >= 16 and _longest_run(sa, sb, 16) >= 16:
            return True
        if len(sb) < ngram:
            continue
        for i in range(len(sb) - ngram + 1):
            recent_ngrams.add(sb[i : i + ngram])
    if not recent_ngrams:
        return False
    hits = 0
    seen = set()
    for i in range(len(sa) - ngram + 1):
        gram = sa[i : i + ngram]
        if gram in recent_ngrams and gram not in seen:
            seen.add(gram)
            hits += 1
            if hits >= min_hits:
                return True
    return False


def probe_chinese_input_understood(system_prompt: str, ui_lang: str) -> bool:
    """非中文 UI 的系统提示是否包含「听懂任意语言输入（含中文）」契约。"""
    from core.i18n import normalize_ui_language

    lang = normalize_ui_language(ui_lang)
    if lang == "zh":
        return "输入理解" in (system_prompt or "") or "输出语言" in (system_prompt or "")
    markers = (
        "INPUT UNDERSTANDING",
        "入力理解",
        "입력 이해",
        "COMPREENSÃO DA ENTRADA",
        "COMPRENSIÓN DE ENTRADA",
        "PEMAHAMAN INPUT",
        "Chinese",
        "中国語",
        "중국어",
        "chinês",
        "chino",
        "Mandarin",
    )
    text = system_prompt or ""
    return any(m in text for m in markers)


def run_probe_bundle(sample: Dict[str, Any]) -> Dict[str, Any]:
    """一次性跑一批探针，供埋点/单测。"""
    return {
        "leave_heavy_ok": probe_leave_heavy(
            bool(sample.get("light_path")), bool(sample.get("has_leave_intent"))
        ),
        "summary_ok": probe_summary_due_updated(
            bool(sample.get("summary_due")),
            str(sample.get("old_summary") or ""),
            str(sample.get("new_summary") or ""),
        ),
        "near_dup": probe_near_duplicate(
            str(sample.get("prev_assistant") or ""),
            str(sample.get("assistant") or ""),
        ),
        "zh_input_ok": probe_chinese_input_understood(
            str(sample.get("system_prompt") or ""),
            str(sample.get("ui_lang") or "zh"),
        ),
    }
