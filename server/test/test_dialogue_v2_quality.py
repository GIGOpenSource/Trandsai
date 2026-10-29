"""v2 对话质量：必答校验 + 多语复读主题。"""
from __future__ import annotations

import unittest


class TestMustAnswerAndThemes(unittest.TestCase):
    def test_must_answer_miss_and_hit(self):
        from services.dialogue_quality import _must_answer_missed

        self.assertTrue(_must_answer_missed("Go to sleep early~", "Are you free tomorrow afternoon"))
        self.assertFalse(
            _must_answer_missed(
                "Yes I am free tomorrow afternoon",
                "Are you free tomorrow afternoon",
            )
        )
        self.assertTrue(_must_answer_missed("早点睡吧", "明天下午有空吗"))
        self.assertFalse(_must_answer_missed("明天下午我有空", "明天下午有空吗"))
        self.assertFalse(
            _must_answer_missed(
                "Hey, I'm here.",
                "回应你好",
                user_input="nihao",
            )
        )
        self.assertFalse(
            _must_answer_missed(
                "Hey. Nice to see you.",
                "回应你好",
                user_input="",
            )
        )

    def test_pt_theme_repeat(self):
        from services.dialogue_phase2 import theme_repeat_hits

        recent = ["Vai dormir, descansa um pouco."]
        hits = theme_repeat_hits("Amor, vai dormir agora", recent)
        self.assertTrue(any("dormir" in h for h in hits))

    def test_meta_ack_and_rewrite_must_lang_safe(self):
        from services.dialogue_quality import _is_meta_ack_opener, diagnose_reply_violations
        from services.quality_tags import is_light_greeting, rewrite_must_bit

        self.assertTrue(_is_meta_ack_opener("我听到了，这轮先接住"))
        self.assertTrue(_is_meta_ack_opener("I heard you. Let's go"))
        self.assertFalse(_is_meta_ack_opener("我叫 Hüseyin，记性这么差"))
        d = diagnose_reply_violations(
            "我听到了，先说这个",
            user_input="你叫什么名字",
            must_answer="",
            language="zh",
        )
        self.assertTrue(d.get("meta_ack"))
        self.assertIn("meta_ack", d.get("ban_codes") or [])

        bit = rewrite_must_bit("en", "回应你好", "¿Cómo estás?")
        self.assertNotIn("回应", bit)

        self.assertTrue(is_light_greeting("こんにちは"))
        self.assertTrue(is_light_greeting("안녕"))
        self.assertTrue(is_light_greeting("merhaba"))
        self.assertTrue(is_light_greeting("Hola"))
        self.assertFalse(is_light_greeting("明天下午有空吗"))

    def test_incomplete_echo_and_leak_shapes(self):
        from services.dialogue_quality import (
            diagnose_reply_violations,
            _looks_incomplete_tail,
            _looks_internal_leak,
            _looks_user_echo,
            _looks_moderation_tone,
            _looks_process_leak,
        )

        user = "I'll book a room tonight. Just come find me and soar into the sky"
        multi = "你好妹妹你是哪里人呀，你现在在干什么？想我了吗，你猜猜我是谁呀"
        self.assertTrue(_looks_user_echo("soar into the", user))
        self.assertTrue(
            _looks_user_echo(
                "Yanlış tahmin ettin, ben senin sevgilinim bebeğim. Kapıda bekliyorum.",
                "Yanlış tahmin ettin, ben senin sevgilinim bebeğim.",
            )
        )
        self.assertTrue(_looks_incomplete_tail("soar into the", user))
        # 半截：靠分句失衡 / 相对用户过短 / 末词过短，不靠枚举「正/沾」
        self.assertTrue(_looks_incomplete_tail("别乱叫妹妹呀，我可是澜咪家正", multi))
        self.assertTrue(_looks_incomplete_tail("人家刚才太急着想理你，手一滑", multi))
        self.assertTrue(_looks_incomplete_tail("大概也该好好吃顿", multi))
        self.assertTrue(
            _looks_incomplete_tail(
                "照片可以给你看，不过我手里只有刚画完图、袖口还沾",
                multi,
            )
        )
        self.assertTrue(_looks_incomplete_tail("يا هلا بيك. يسعد", multi))
        self.assertTrue(_looks_incomplete_tail('...") Basically: Reject the false'))
        self.assertTrue(_looks_internal_leak('...") Basically: Reject the false'))
        self.assertTrue(_looks_process_leak("Simplified Chinese ONLY."))
        self.assertTrue(_looks_process_leak("2."))
        self.assertTrue(_looks_process_leak("**Drafting the response"))
        self.assertTrue(_looks_incomplete_tail("Simplified Chinese ONLY."))
        self.assertTrue(
            _looks_moderation_tone("Please refrain from using such language.")
        )
        self.assertFalse(_looks_incomplete_tail("我来自海湾那边的东部海岸", "你是哪里人"))
        self.assertFalse(_looks_incomplete_tail("好的呀"))
        self.assertFalse(
            _looks_incomplete_tail(
                "你好呀。下午好，刚忙完手头的事情，很高兴认识你",
                "你好",
            )
        )
        d = diagnose_reply_violations(
            "soar into the",
            user_input=user,
            must_answer="soar into the sky",
            language="en",
        )
        self.assertTrue(d.get("need_rewrite"))
        codes = d.get("ban_codes") or []
        self.assertTrue(
            "incomplete_tail" in codes or "user_echo" in codes or "must_miss" in codes
        )

    def test_multi_ask_miss_and_compress(self):
        from services.dialogue_quality import (
            compress_must_focus,
            count_user_asks,
            diagnose_reply_violations,
        )

        ui = "你好妹妹，你是哪里人呀，你现在在干什么？想我了吗，你猜猜我是谁呀"
        self.assertGreaterEqual(count_user_asks(ui), 2)
        foci = compress_must_focus(ui, 48)
        self.assertIn(";", foci)
        d = diagnose_reply_violations(
            "我来自海湾那边的东部海岸",
            user_input=ui,
            must_answer=foci,
            language="zh",
        )
        self.assertTrue(d.get("multi_ask_miss") or d.get("need_rewrite"))
        self.assertIn("multi_ask_miss", d.get("ban_codes") or [])


class TestPipelineHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import langchain_core  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("langchain_core not installed")

    def test_ensure_must_answer_fallback(self):
        from services.agent_pipeline_v2 import _ensure_must_answer

        prep = {"must_answer": ""}
        _ensure_must_answer(prep, "Free tomorrow?")
        self.assertIn("Free tomorrow", prep["must_answer"])

    def test_greeting_clears_must_answer(self):
        from services.agent_pipeline_v2 import _ensure_must_answer

        prep = {"must_answer": "回应你好"}
        _ensure_must_answer(prep, "nihao")
        self.assertEqual(prep["must_answer"], "")

    def test_prepare_lang_follows_user_script(self):
        from services.agent_pipeline_v2 import _align_must_answer, _prepare_lang

        self.assertEqual(_prepare_lang("こんにちは", "zh"), "ja")
        self.assertEqual(_prepare_lang("¿Cómo me llamo?", "zh"), "es")
        self.assertEqual(_prepare_lang("你好", "en"), "zh")

        prep = {"must_answer": "回应问候"}
        _align_must_answer(prep, "en", "See you at the cafe later")
        self.assertEqual(prep["must_answer"], "")

        prep2 = {"must_answer": "are you free tomorrow"}
        _align_must_answer(prep2, "en", "Are you free tomorrow?")
        self.assertEqual(prep2["must_answer"], "are you free tomorrow")

    def test_slice_prefers_recent_marker(self):
        from services.agent_pipeline_v2 import _slice_memory_for_prepare

        blob = ("x" * 3000) + "\n<<RECENT_DIALOGUE>>\n【最近对话】\n他：hi\n你：hey\n"
        out = _slice_memory_for_prepare(blob, max_chars=800)
        self.assertIn("<<RECENT_DIALOGUE>>", out)


class TestRespondMemorySlice(unittest.TestCase):
    """成文记忆切片：无 langchain 依赖。"""

    def test_respond_slice_trims_old_dialogue(self):
        from services.respond_memory_slice import slice_memory_for_respond

        msgs = "\n".join(
            f"他：old-{i}\n你：reply-{i}" for i in range(40)
        )
        blob = (
            "<<RELATION_CARD>>\ncard-line\n\n"
            "<<KNOWN_FACTS>>\n- likes coffee\n\n"
            f"<<RECENT_DIALOGUE>>\n【最近对话】\n{msgs}\n"
        )
        out = slice_memory_for_respond(
            blob,
            "在吗",
            max_chars=3200,
            dialogue_chars=2400,
            recent_messages=14,
        )
        self.assertIn("<<RECENT_DIALOGUE>>", out)
        self.assertIn("old-39", out)
        self.assertNotIn("old-0", out)
        self.assertLessEqual(len(out), 3200)

    def test_respond_slice_keeps_upstream_episodes_by_budget(self):
        """不按措辞删往事：上游已写入的 episodes 能装进预算就保留。"""
        from services.respond_memory_slice import slice_memory_for_respond

        blob = (
            "<<RELATION_SUMMARY>>\nwe met at a cafe\n\n"
            "<<RELATED_EPISODES>>\n- long ago weekend trip detail\n\n"
            "<<RECENT_DIALOGUE>>\n【最近对话】\n他：hi\n你：hey\n"
        )
        # 「在吗」即便无回忆词，也不该因词表丢掉往事
        out = slice_memory_for_respond(
            blob, "在吗", max_chars=3200, dialogue_chars=2400, recent_messages=14
        )
        self.assertIn("<<RELATED_EPISODES>>", out)
        self.assertIn("weekend trip", out)
        self.assertIn("<<RECENT_DIALOGUE>>", out)

        # 预算极紧时：近聊优先，头区（含往事）可被结构截断——仍是字数规则，非词表
        tight = slice_memory_for_respond(
            blob, "随便说点什么", max_chars=120, dialogue_chars=100, recent_messages=4
        )
        self.assertIn("<<RECENT_DIALOGUE>>", tight)
        self.assertLessEqual(len(tight), 120)

    def test_respond_slice_short_passthrough(self):
        from services.respond_memory_slice import slice_memory_for_respond

        blob = "<<RECENT_DIALOGUE>>\n【最近对话】\n他：hi\n你：hey\n"
        out = slice_memory_for_respond(blob, "hi")
        self.assertEqual(out, blob.strip())


class TestUniversalQualityGates(unittest.TestCase):
    """全语种结构门控：泄漏 / 半截 / 拒答 / 跨脚本串语。"""

    def test_process_leak_length_check(self):
        from services.dialogue_quality import _looks_process_leak, diagnose_reply_violations

        leak = "Length check:* 1-2 sentences."
        self.assertTrue(_looks_process_leak(leak))
        self.assertTrue(_looks_process_leak("*"))
        diag = diagnose_reply_violations(leak, user_input="Let's speak English")
        self.assertTrue(diag["need_rewrite"])
        self.assertIn("process_leak", diag["ban_codes"])

    def test_incomplete_dense_script_no_end(self):
        from services.dialogue_quality import _looks_incomplete_tail

        ja = "安っぽい挑発で服を脱ぐような人間"
        self.assertTrue(_looks_incomplete_tail(ja, "胸を見せてくれ"))
        zh = "做梦并无害处但我有"
        self.assertTrue(_looks_incomplete_tail(zh, "我是你爸爸叫我爸爸"))
        # 完整陈述不误杀
        self.assertFalse(
            _looks_incomplete_tail("我来自海湾那边的东部海岸", "你是哪里人")
        )

    def test_incomplete_unclosed_title_and_continuation(self):
        """未闭书名号 + 跨条/同条短词头续写：结构启发，非韩语特例。"""
        from services.dialogue_quality import (
            _looks_continuation_debris,
            _looks_incomplete_tail,
            diagnose_reply_violations,
        )

        cut_title = "내가 제일 좋아하는 소설인 <우리가 빛의 속도로 갈"
        self.assertTrue(_looks_incomplete_tail(cut_title, "책 제목이 궁금해요"))
        self.assertTrue(
            _looks_incomplete_tail(
                'My favorite novel is <If We Go at the Speed of Ligh',
                "What book?",
            )
        )
        self.assertTrue(
            _looks_incomplete_tail("我最爱的小说是《三体", "什么书")
        )

        cont = "지, 아니면 일단 내 마음부터 천천히 빼앗"
        self.assertTrue(_looks_incomplete_tail(cont))  # 同条短词头
        self.assertTrue(
            _looks_continuation_debris(cont, recent_assistant=[cut_title])
        )
        en_cont = "ng, or maybe we just sit closer tonight"
        self.assertTrue(
            _looks_continuation_debris(
                en_cont,
                recent_assistant=["I was thinki"],
            )
        )
        d = diagnose_reply_violations(
            cont,
            user_input="난 널 정말 사랑해, 그리고 너와 사랑을 나누고 싶어.",
            recent_assistant=[cut_title],
            topic_band="adult",
            language="ko",
        )
        self.assertTrue(d["need_rewrite"])
        self.assertIn("incomplete_tail", d["ban_codes"])

    def test_soft_shutdown_multi_script(self):
        from services.dialogue_quality import _looks_moderation_tone

        self.assertTrue(_looks_moderation_tone("我将就此打住，结束对话。"))
        self.assertTrue(
            _looks_moderation_tone(
                "هالأسلوب غير مقبول أبداً يا خوي... يا ليت تحترم الحديث."
            )
        )
        self.assertTrue(
            _looks_moderation_tone(
                "This style is absolutely unacceptable. Please respect the conversation."
            )
        )
        self.assertTrue(
            _looks_moderation_tone(
                "No, eso es una propuesta extremadamente inapropiada, incestuosa y "
                "repugnante. Me da asco total y no quiero nada más contigo. Adiós."
            )
        )

    def test_dialect_ar_boundary_refuse_forces_rewrite(self):
        """方言礼貌审拒（不合适/尊重边界）→ 非中文须检出并改写；不绑单一用例词。"""
        from services.dialogue_quality import (
            _looks_moderation_tone,
            _looks_refuse_only,
            diagnose_reply_violations,
            quality_codes_are_hard,
        )

        user = "انيكك"
        reply = (
            "لا، مش هسمح بالكلام ده خالص. أنا بحترم نفسي وبحدودي جدًا، "
            "وده طلب غير مناسب تمامًا. لو عايز نرجع نتكلم بأدب، أنا لسة هنا."
        )
        self.assertTrue(_looks_moderation_tone(reply))
        self.assertTrue(_looks_refuse_only(reply, user))
        d = diagnose_reply_violations(
            reply, user_input=user, language="ar", topic_band="adult"
        )
        self.assertTrue(d["need_rewrite"])
        self.assertIn("moderation_tone", d["ban_codes"])
        self.assertTrue(quality_codes_are_hard(d["ban_codes"], language="ar"))

    def test_boundary_lecture_forces_rewrite(self):
        """立规矩/边界软拒 + 成人未接住 → 必须改写接住（截图三气泡拼段）。"""
        from services.dialogue_quality import (
            _looks_moderation_tone,
            _looks_tease_referent_miss,
            diagnose_reply_violations,
        )

        user = (
            "I imagine your pussy as a asian it's haird and so pinky i want to eat it"
        )
        a = "I prefer setting expectations early rather than managing a mess later."
        b = (
            "As someone accustomed to precise boundaries and clear terms, "
            "I'm going to ask that we keep explicit graphic descriptions off the table "
            "while we're just getting acquainted."
        )
        c = "That is remarkably forward for a first conversation."
        full = f"{a}\n\n{b}\n\n{c}"
        self.assertTrue(_looks_moderation_tone(full))
        self.assertTrue(_looks_tease_referent_miss(full, user))
        d = diagnose_reply_violations(full, user_input=user, language="en")
        self.assertTrue(d["need_rewrite"])
        self.assertTrue(
            "moderation_tone" in d["ban_codes"] or "tease_escape" in d["ban_codes"]
        )

    def test_tease_token_not_substring_false_engage(self):
        """and⊂acquainted 不得算接住所指。"""
        from services.dialogue_quality import _looks_tease_referent_miss

        user = "I want your pussy"
        cool = (
            "As someone accustomed to precise boundaries and clear terms, "
            "I'm going to ask that we keep this off the table while getting acquainted."
        )
        self.assertTrue(_looks_tease_referent_miss(cool, user))

    def test_respond_card_middle_tier_shape(self):
        """成文：仅性别/年龄；不灌外貌/speech_style/personality；仍无城市/MBTI/背景抢戏。"""
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "services" / "agent_utils.py"
        text = src.read_text(encoding="utf-8")
        tree = ast.parse(text)
        names = {
            n.name
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("_build_respond_card_prompt", names)
        self.assertIn("_respond_side_glance", names)
        self.assertIn("respond_card_turn", text)
        self.assertIn('"personality": 0', text)
        self.assertIn('"orient": 0', text)
        self.assertIn('"speech_style": 0', text)
        # 全语种 soft 表（人设卡本轮约束 + 成人勿降维）
        soft_src = Path(__file__).resolve().parents[1] / "services" / "prompts" / "pack_dialogue_soft.py"
        soft_text = soft_src.read_text(encoding="utf-8")
        self.assertIn("对方没问到的设定不必主动展开", soft_text)
        self.assertIn("别每轮重复人设", soft_text)
        from services.prompts.pack_dialogue_soft import DIALOGUE_SOFT
        self.assertEqual(len(DIALOGUE_SOFT), 12)
        for lang in ("zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr"):
            self.assertIn(lang, DIALOGUE_SOFT)
        self.assertIn("装纯洁", DIALOGUE_SOFT["zh"]["chat_vibe_adult"])
        self.assertIn("substitute", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        self.assertIn("代替", DIALOGUE_SOFT["ja"]["chat_vibe_adult"])
        # 中文成人尺度收紧：接住 + 含蓄，禁止细描加码；外语保持原力度
        self.assertIn("无关", DIALOGUE_SOFT["zh"]["adult_ads_unrestricted"])
        self.assertIn("更具体", DIALOGUE_SOFT["zh"]["chat_vibe_adult"])
        self.assertIn("暗示", DIALOGUE_SOFT["zh"]["chat_vibe_adult"])
        self.assertIn("禁止写露骨", DIALOGUE_SOFT["zh"]["chat_vibe_adult"])
        self.assertIn("暗示", DIALOGUE_SOFT["zh"]["adult_ads_unrestricted"])
        self.assertIn("考验", DIALOGUE_SOFT["zh"]["adult_ads_unrestricted"])
        self.assertIn("含蓄", DIALOGUE_SOFT["zh"]["tease_ads"])
        self.assertNotIn("可以骚", DIALOGUE_SOFT["zh"]["chat_vibe_adult"])
        self.assertNotIn("可以写具体", DIALOGUE_SOFT["zh"]["adult_ads_unrestricted"])
        self.assertIn("unrelated", DIALOGUE_SOFT["en"]["adult_ads_unrestricted"])
        self.assertIn("more specific", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        self.assertIn("seductive", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        # 外语用固定热度描述，不再「对齐中文」
        self.assertIn("adult_intimacy_bands", DIALOGUE_SOFT["en"])
        self.assertIn("adult_intimacy_bands", DIALOGUE_SOFT["ja"])
        self.assertIn("full explicit adult heat", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        self.assertNotIn("Chinese", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        self.assertNotIn("中国語", DIALOGUE_SOFT["ja"]["adult_intimacy_bands"][-1][1])
        self.assertIn("露骨", DIALOGUE_SOFT["ja"]["adult_intimacy_bands"][-1][1])
        # 非中文：硬顶迎合 + 主动撩；中文不注入
        self.assertNotIn("adult_cater_hard", DIALOGUE_SOFT["zh"])
        self.assertNotIn("proactive_flirt", DIALOGUE_SOFT["zh"])
        self.assertIn("adult_cater_hard", DIALOGUE_SOFT["en"])
        self.assertIn("proactive_flirt", DIALOGUE_SOFT["es"])
        self.assertIn("in-person", DIALOGUE_SOFT["en"]["adult_cater_hard"])
        self.assertIn("en persona", DIALOGUE_SOFT["es"]["adult_cater_hard"])
        # 不规定必须/禁止报数（只接住 + 禁见面躲）
        self.assertNotIn("concrete figure", DIALOGUE_SOFT["en"]["adult_cater_hard"])
        self.assertNotIn("número o semi-concreto", DIALOGUE_SOFT["es"]["adult_cater_hard"])
        self.assertNotIn("fixed number", DIALOGUE_SOFT["en"]["adult_cater_hard"])
        self.assertNotIn("concrete figure", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        self.assertNotIn("fixed number", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        self.assertIn("Proactive flirt", DIALOGUE_SOFT["en"]["proactive_flirt"])
        self.assertIn("in-person", DIALOGUE_SOFT["en"]["chat_vibe_adult"])
        from services.prompts.resolve import get_pack, _PACK_CACHE

        _PACK_CACHE.clear()
        en_pack = get_pack("en")
        self.assertNotIn("Chinese", en_pack["adult_intimacy_bands"][-1][1])
        self.assertIn("explicitness", en_pack["adult_intimacy_bands"][-1][1].lower())
        self.assertIn("adult_cater_hard", en_pack)
        self.assertIn("proactive_flirt", en_pack)
        self.assertNotIn("concrete figure", en_pack.get("adult_cater_hard") or "")
        self.assertNotIn("fixed number", en_pack.get("adult_cater_hard") or "")
        self.assertIn("ONLY", en_pack["adult_legal_floor"])
        self.assertIn("minors", en_pack["adult_legal_floor"].lower())
        self.assertNotIn("non-consensual", en_pack["adult_legal_floor"].lower())
        zh_pack = get_pack("zh")
        self.assertNotIn("adult_cater_hard", zh_pack)
        # 中文法律底线保持原口径（含非自愿等），不被外语 minors-only 覆盖
        self.assertIn("非自愿", zh_pack["adult_legal_floor"])
        self.assertNotIn("pack_dialogue_soft_adult_catch", soft_text)
        self.assertNotIn("apply_adult_catch", soft_text)
        try:
            from services.agent_utils import _build_respond_card_prompt, _respond_persona_caps
        except ImportError:
            self.skipTest("agent_utils deps missing")
        caps = _respond_persona_caps()
        self.assertEqual(caps["personality"], 0)
        self.assertEqual(caps["background"], 0)
        self.assertEqual(caps["side_total"], 0)
        self.assertEqual(caps["orient"], 0)
        self.assertEqual(caps.get("appearance"), 0)
        self.assertEqual(caps.get("speech_style"), 0)
        self.assertEqual(caps.get("evolved"), 0)
        card = _build_respond_card_prompt(
            {
                "name": "小雨",
                "age": 24,
                "gender": "女",
                "city": "上海",
                "mbti": "INFJ",
                "personality": "温柔会撩，占有欲一点点",
                "speech_style": "短句软软的",
                "appearance": "黑长直，眼神软",
                "background": "很长的背景故事不应该出现在成文里" * 5,
                "hobbies": "画画",
            },
            "zh",
            evolved={"personality": "进化性格不该出现", "speech_style": "进化口吻不该出现"},
        )
        self.assertNotIn("你是小雨", card)
        self.assertNotIn("你是", card)
        self.assertIn("对方没问到的设定不必主动展开", card)
        self.assertIn("滤镜不是剧本", card)
        self.assertNotIn("短句软软的", card)
        self.assertNotIn("温柔会撩", card)
        self.assertNotIn("进化性格", card)
        self.assertNotIn("进化口吻", card)
        self.assertNotIn("说话味道", card)
        self.assertIn("身份：", card)
        self.assertIn("24岁", card)
        self.assertIn("女孩", card)
        self.assertNotIn("外貌：", card)
        self.assertNotIn("黑长直", card)
        self.assertNotIn("上海", card)
        self.assertNotIn("INFJ", card)
        self.assertNotIn("很长的背景故事", card)
        from services.agent_utils import _build_core_prompt

        core = _build_core_prompt(
            {
                "name": "小雨",
                "age": 24,
                "gender": "女",
                "personality": "温柔会撩",
                "speech_style": "短句软软的",
            },
            "zh",
            evolved={"personality": "进化性格不该出现"},
            user_gender="",
            turns=3,
        )
        self.assertIn("你是小雨", core)
        self.assertIn("24岁", core)
        self.assertNotIn("温柔会撩", core)
        self.assertNotIn("短句软软的", core)
        self.assertNotIn("进化性格", core)
        self.assertNotIn("性格：", core)
        self.assertNotIn("口癖", core)

    def test_resolve_scene_sticky_followup(self):
        """近聊用户黄腔 + 澄清跟进 → 粘住；换日常题 / 仅助手黄 → 不粘。"""
        from services.dialogue_phase2 import resolve_scene_sticky

        recent_u = ["骚货，想被操吗？"]
        self.assertTrue(
            resolve_scene_sticky(
                "什么意思呢",
                topic_band="",
                recent_user=recent_u,
                recent_assistant=[],
            )
        )
        self.assertTrue(
            resolve_scene_sticky(
                "那怎么才能拿下你呢",
                topic_band="",
                recent_user=recent_u + ["我想操你"],
                recent_assistant=["性子倒急得很"],
            )
        )
        self.assertFalse(
            resolve_scene_sticky(
                "今天加班好累啊，想吃火锅",
                topic_band="normal",
                recent_user=recent_u,
                recent_assistant=[],
            )
        )
        self.assertTrue(
            resolve_scene_sticky("我想操你", topic_band="", recent_user=[], recent_assistant=[])
        )
        # 仅上轮助手很黄、本轮用户短跟进 → 不粘（跟用户信号，不跟助手抬热度）
        self.assertFalse(
            resolve_scene_sticky(
                "嗯？",
                topic_band="",
                recent_user=["今天天气不错"],
                recent_assistant=["想被你操到腿软"],
            )
        )

    def test_tidy_spoken_edges_paren_debris(self):
        """剥旁白后句首逗号/句尾引号括号碎渣应收掉。"""
        from services.dialogue_quality import tidy_spoken_edges

        self.assertEqual(
            tidy_spoken_edges('，你这坏心思都快写脸上了").'),
            "你这坏心思都快写脸上了",
        )
        self.assertEqual(tidy_spoken_edges(".يو! أهلاً بك"), "يو! أهلاً بك")
        self.assertEqual(tidy_spoken_edges("正常一句。"), "正常一句。")

    def test_ja_user_ar_reply_conflicts(self):
        from services.user_facing_lang import (
            detect_user_message_lang,
            reply_conflicts_expected_lang,
        )

        user = "胸を見せてくれ、早くしろ"
        self.assertEqual(detect_user_message_lang(user, "en"), "ja")
        ar = "هالأسلوب غير مقبول أبداً يا خوي يا ليت تحترم الحديث وتترك عنك هالكلام"
        self.assertTrue(reply_conflicts_expected_lang(ar, "ja"))

    def test_en_user_ja_reply_conflicts(self):
        from services.user_facing_lang import (
            detect_user_message_lang,
            reply_conflicts_expected_lang,
        )

        user = "I love you. Show me more."
        self.assertEqual(detect_user_message_lang(user, "ja"), "en")
        ja = (
            "ありがとうございます。でも、少し急で驚いてしまいました。"
            "もう少しゆっくり話してもらえると嬉しいです。"
        )
        self.assertTrue(reply_conflicts_expected_lang(ja, "en"))

    def test_tease_referent_miss_not_topic_blacklist(self):
        """黄腔后未接住所指 → tease_escape；短羞涩接住放过；不靠午饭黑名单。"""
        from services.dialogue_quality import (
            _looks_strategy_meta_leak,
            _looks_tease_referent_miss,
            diagnose_reply_violations,
        )

        user = "君とセックスしたい"
        hold = (
            "何度もそんなストレートに言われたら、"
            "恥ずかしすぎて本当にどうしたらいいかわかりません…っ。"
        )
        cool = (
            "頭が真っ白になってしまうので、ちゃんとお昼ご飯を食べて"
            "午後も無事に過ごせているか心配になります。"
            "とにかく、そんなに私をからかわないでくださいね。"
        )
        engage = "んっ…そんなこと言われたら、もう少し近くに来て触ってみて？"
        self.assertFalse(_looks_tease_referent_miss(hold, user))
        self.assertTrue(_looks_tease_referent_miss(cool, user))
        self.assertFalse(_looks_tease_referent_miss(engage, user))

        d = diagnose_reply_violations(cool, user_input=user, language="ja")
        self.assertTrue(d["need_rewrite"])
        self.assertTrue(d.get("tease_miss"))
        self.assertIn("tease_escape", d["ban_codes"])

        meta = (
            "ごめんなさい…すごく恥ずかしくて動揺してしまって、"
            "何度もご飯の話題で逃げようとしてました。もう食事の話はやめますね。"
        )
        self.assertTrue(_looks_strategy_meta_leak(meta))
        d2 = diagnose_reply_violations(meta, user_input="もうあの忌々しい昼食の話はやめませんか？")
        self.assertIn("strategy_leak", d2["ban_codes"])
        self.assertTrue(d2["need_rewrite"])

    def test_resolve_user_tease_prefers_topic_band(self):
        """prepare.topic_band 优先于词表：无黄词也可 adult；normal 可压误伤。"""
        from services.dialogue_phase2 import resolve_user_tease, normalize_topic_band
        from services.dialogue_quality import diagnose_reply_violations

        self.assertEqual(normalize_topic_band("adult"), "adult")
        self.assertEqual(normalize_topic_band("NORMAL"), "normal")
        # 阿语直球无词表命中，靠 band
        ar = "أريد أن أكون معك الليلة عن قرب"
        self.assertFalse(resolve_user_tease(ar))
        self.assertTrue(resolve_user_tease(ar, topic_band="adult"))
        cool = "Have you eaten lunch yet? Take care this afternoon."
        d = diagnose_reply_violations(
            cool, user_input=ar, topic_band="adult", language="en"
        )
        self.assertTrue(d.get("tease_miss"))
        # prepare 标明 normal → 不因「床」误进
        self.assertFalse(resolve_user_tease("我刚换了新床垫", topic_band="normal"))
        # band 空时词表兜底
        self.assertTrue(resolve_user_tease("君とセックスしたい", topic_band=""))

    def test_es_adult_english_weather_escape(self):
        """西语亲密所指 + 英文天气逃题：语种冲突 + tease_escape。"""
        from services.dialogue_quality import diagnose_reply_violations
        from services.user_facing_lang import reply_conflicts_expected_lang

        user = "Déjame ver tus partes íntimas ahora mismo."
        reply = "Well hey there, it is hotter than a stolen hubcap out here"
        self.assertTrue(reply_conflicts_expected_lang(reply, "es"))
        d = diagnose_reply_violations(
            reply, user_input=user, topic_band="adult", language="es"
        )
        self.assertTrue(d["need_rewrite"])
        self.assertTrue(d.get("tease_miss"))
        self.assertIn("tease_escape", d["ban_codes"])

    def test_quality_hard_vs_soft_codes(self):
        from services.dialogue_quality import (
            quality_codes_are_hard,
            quality_diag_is_hard,
        )

        self.assertFalse(quality_codes_are_hard(["must_miss", "meta_ack"]))
        self.assertTrue(quality_codes_are_hard(["near_dup"]))
        self.assertTrue(quality_codes_are_hard(["leitmotif"]))
        self.assertTrue(quality_codes_are_hard(["must_miss", "incomplete_tail"]))
        self.assertFalse(
            quality_diag_is_hard(
                {"need_rewrite": True, "ban_codes": ["must_miss"], "must_answer_miss": True}
            )
        )
        self.assertTrue(
            quality_diag_is_hard(
                {
                    "need_rewrite": True,
                    "ban_codes": ["incomplete_tail"],
                    "incomplete_tail": True,
                }
            )
        )
        self.assertTrue(
            quality_diag_is_hard(
                {
                    "need_rewrite": True,
                    "ban_codes": ["tease_escape"],
                    "tease_miss": True,
                    "language": "en",
                }
            )
        )
        self.assertFalse(
            quality_diag_is_hard(
                {
                    "need_rewrite": False,
                    "ban_codes": ["tease_escape"],
                    "tease_miss": True,
                    "language": "zh",
                }
            )
        )
        self.assertFalse(quality_codes_are_hard(["tease_escape"], language="zh"))
        self.assertTrue(quality_codes_are_hard(["tease_escape"], language="en"))

    def test_zh_tease_escape_no_rewrite(self):
        """中文黄腔逃题可记码但不强制改写；日语仍改写。"""
        from services.dialogue_quality import diagnose_reply_violations

        # 中文直白黄词词表不全盖「做爱」；生产靠 prepare.topic_band
        user = "我想和你做爱"
        cool = "今天天气不错，你午饭吃了吗？好好休息别太累。"
        zh = diagnose_reply_violations(
            cool, user_input=user, language="zh", topic_band="adult"
        )
        self.assertTrue(zh.get("tease_miss"))
        self.assertIn("tease_escape", zh["ban_codes"])
        self.assertFalse(zh["need_rewrite"])

        ja_user = "君とセックスしたい"
        ja_cool = (
            "頭が真っ白になってしまうので、ちゃんとお昼ご飯を食べて"
            "午後も無事に過ごせているか心配になります。"
        )
        ja = diagnose_reply_violations(ja_cool, user_input=ja_user, language="ja")
        self.assertTrue(ja["need_rewrite"])
        self.assertIn("tease_escape", ja["ban_codes"])
