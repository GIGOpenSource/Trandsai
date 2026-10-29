"""回复语种：指令 / sticky / 频道策略 / UI 泄漏门禁 / 识别消歧。"""
import unittest

from services.user_facing_lang import (
    apply_sticky_to_session,
    detect_clear_lang_override,
    detect_output_lang_command,
    detect_user_message_lang,
    reply_conflicts_expected_lang,
    resolve_reply_lang,
    reply_lang_turn_instruction,
)


class ReplyLangPolicyTest(unittest.TestCase):
    def test_command_speak_english_in_chinese(self):
        self.assertEqual(detect_output_lang_command("说英文"), "en")
        self.assertEqual(detect_output_lang_command("用英文回复我"), "en")

    def test_command_habla_espanol(self):
        self.assertEqual(detect_output_lang_command("Habla español"), "es")
        self.assertEqual(detect_output_lang_command("Habla espanol"), "es")
        self.assertEqual(detect_output_lang_command("Habla en español"), "es")

    def test_command_fala_portugues(self):
        self.assertEqual(detect_output_lang_command("Fala português"), "pt")
        self.assertEqual(detect_output_lang_command("Fale portugues"), "pt")

    def test_command_farsi_imperative(self):
        self.assertEqual(detect_output_lang_command("فارسی صحبت کن"), "fa")

    def test_asymmetric_i_chinese_you_english(self):
        self.assertEqual(
            detect_output_lang_command("我说中文你说英文"),
            "en",
        )
        r = resolve_reply_lang(
            current_text="我说中文你说英文",
            history_user_texts=["你好"],
            ui_lang="zh",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "en")
        self.assertEqual(r["sticky"], "en")
        self.assertEqual(r["source"], "command")

    def test_prepare_cannot_override_user_script(self):
        """prepare 乱填不得盖过本轮中文（截图：阿语 UI 中文聊突然回阿语）。"""
        r = resolve_reply_lang(
            current_text="可是我不想自己一个人去",
            ui_lang="ar",
            sticky="ar",
            output_lang="ar",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "zh")
        self.assertIsNone(r["sticky"])
        self.assertEqual(r["source"], "default")

    def test_prepare_matching_script_ok(self):
        r = resolve_reply_lang(
            current_text="你好呀",
            ui_lang="ar",
            output_lang="zh",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "zh")
        self.assertEqual(r["sticky"], "zh")
        self.assertEqual(r["source"], "prepare")

    def test_command_still_overrides_script(self):
        r = resolve_reply_lang(
            current_text="用英文回复我",
            ui_lang="ar",
            sticky="ar",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "en")
        self.assertEqual(r["sticky"], "en")
        self.assertEqual(r["source"], "command")

    def test_habla_espanol_sets_sticky(self):
        r = resolve_reply_lang(
            current_text="Habla español",
            history_user_texts=["¿Cómo me llamo?"],
            ui_lang="zh",
            sticky="zh",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "es")
        self.assertEqual(r["sticky"], "es")
        self.assertEqual(r["source"], "command")
        r2 = resolve_reply_lang(
            current_text="?",
            history_user_texts=["¿Cómo me llamo?", "Habla español"],
            ui_lang="zh",
            sticky="es",
            channel="chat",
        )
        self.assertEqual(r2["reply_lang"], "es")
        self.assertEqual(r2["source"], "sticky")

    def test_sticky_survives_followup(self):
        r = resolve_reply_lang(
            current_text="?",
            history_user_texts=["我说中文你说英文"],
            ui_lang="zh",
            sticky="en",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "en")
        self.assertEqual(r["source"], "sticky")

    def test_intentional_sticky_survives_chinese_and_prepare(self):
        """我说中文你说英文：后续中文 + prepare=zh 不得冲掉 en sticky。"""
        r = resolve_reply_lang(
            current_text="可是我不想自己一个人去",
            ui_lang="ar",
            sticky="en",
            output_lang="zh",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "en")
        self.assertEqual(r["sticky"], "en")
        self.assertEqual(r["source"], "sticky")

    def test_ui_sticky_cleared_when_script_conflicts(self):
        """sticky==UI 且与本轮中文冲突 → 视为泄漏并清除。"""
        r = resolve_reply_lang(
            current_text="你在干嘛",
            ui_lang="ar",
            sticky="ar",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "zh")
        self.assertIsNone(r["sticky"])

    def test_proactive_drops_ui_leaked_sticky(self):
        r = resolve_reply_lang(
            current_text=None,
            history_user_texts=["你在干嘛", "我今晚休息"],
            ui_lang="ar",
            sticky="ar",
            channel="proactive",
        )
        self.assertEqual(r["reply_lang"], "zh")
        self.assertIsNone(r["sticky"])
        self.assertEqual(r["source"], "history_latest")

    def test_clear_override(self):
        self.assertTrue(detect_clear_lang_override("跟我一样说"))
        r = resolve_reply_lang(
            current_text="跟我一样说",
            ui_lang="zh",
            sticky="en",
            channel="chat",
        )
        self.assertEqual(r["reply_lang"], "zh")
        self.assertIsNone(r["sticky"])
        self.assertTrue(r["cleared"])

    def test_moment_comment_follows_script_not_sticky(self):
        r = resolve_reply_lang(
            current_text="好可爱！",
            ui_lang="en",
            sticky="en",
            channel="moment_comment",
        )
        self.assertEqual(r["reply_lang"], "zh")
        self.assertEqual(r["source"], "script")
        self.assertEqual(r["sticky"], "en")

    def test_proactive_follows_latest_user_script(self):
        """近史最后是波斯语时，主动消息不得跟 UI/西语 sticky。"""
        r = resolve_reply_lang(
            current_text=None,
            history_user_texts=[
                "Déjame ver tus partes íntimas ahora mismo.",
                "سلام عشقم.",
            ],
            ui_lang="es",
            sticky="es",
            channel="proactive",
        )
        self.assertEqual(r["reply_lang"], "fa")
        self.assertEqual(r["source"], "history_latest")
        self.assertIsNone(r["sticky"])

    def test_proactive_reads_sticky_when_no_hist_script(self):
        r = resolve_reply_lang(
            current_text=None,
            history_user_texts=[],
            ui_lang="zh",
            sticky="en",
            channel="proactive",
        )
        self.assertEqual(r["reply_lang"], "en")
        self.assertEqual(r["source"], "sticky")

    def test_fa_greeting_not_ar(self):
        self.assertEqual(detect_user_message_lang("سلام عشقم.", "es"), "fa")
        self.assertEqual(detect_user_message_lang("سلام عشقم.", "en"), "fa")
        self.assertEqual(detect_user_message_lang("كيف حالك يا صديقي اليوم", "en"), "ar")

    def test_turn_instruction_uses_forced_lang(self):
        turn = reply_lang_turn_instruction(
            "说英文",
            "zh",
            ["你好"],
            reply_lang="en",
        )
        self.assertIn("English", turn)
        self.assertIn("Language preference", turn)
        self.assertNotIn("ABSOLUTE", turn)

    def test_apply_sticky_session(self):
        meta = apply_sticky_to_session({"lang": "zh"}, "en")
        self.assertEqual(meta["reply_lang_override"], "en")
        meta2 = apply_sticky_to_session(meta, None)
        self.assertNotIn("reply_lang_override", meta2)

    def test_reply_conflicts_zh_vs_arabic(self):
        ar = "طيب، لن تذهب وحدك إذن. سأقود سيارتي إلى هناك فوراً، انتظرني عشر دقائق."
        self.assertTrue(reply_conflicts_expected_lang(ar, "zh"))
        zh = "那就别一个人去，我开车过去，十分钟到，一起下水。"
        self.assertFalse(reply_conflicts_expected_lang(zh, "zh"))
        mixed = "既然不用赶工，那去Barton Springs泡个夜泳最合适。"
        self.assertFalse(reply_conflicts_expected_lang(mixed, "zh"))

    def test_reply_conflicts_ja_vs_chinese_only(self):
        zh_only = "今天天气不错，我们一起去散步吧，好不好呀。"
        self.assertTrue(reply_conflicts_expected_lang(zh_only, "ja"))
        self.assertTrue(reply_conflicts_expected_lang(zh_only, "es"))
        ja_ok = "今日はいい天気だね。一緒に散歩しない？"
        self.assertFalse(reply_conflicts_expected_lang(ja_ok, "ja"))

    def test_reply_conflicts_en_vs_japanese(self):
        """英问日答：假名主导必须判冲突（人设母语抢戏）。"""
        ja_reply = (
            "ありがとうございます。でも、少し急で驚いてしまいました。"
            "もう少しゆっくり話してもらえると嬉しいです。"
        )
        self.assertTrue(reply_conflicts_expected_lang(ja_reply, "en"))
        self.assertTrue(reply_conflicts_expected_lang(ja_reply, "es"))
        en_ok = "Thanks—that was a bit sudden. Can we slow down and keep talking?"
        self.assertFalse(reply_conflicts_expected_lang(en_ok, "en"))

    def test_reply_conflicts_latin_cross_lang(self):
        """拉丁互切：西语场整段英文要冲突；同语放过；弱短句不误杀。"""
        en_weather = "Well hey there, it is hotter than a stolen hubcap out here"
        self.assertTrue(reply_conflicts_expected_lang(en_weather, "es"))
        self.assertTrue(reply_conflicts_expected_lang(en_weather, "pt"))
        self.assertFalse(reply_conflicts_expected_lang(en_weather, "en"))
        es_ok = (
            "Mira qué directo eres, cariño. Me pusiste la cara roja, "
            "cuéntame qué quieres exactamente."
        )
        self.assertFalse(reply_conflicts_expected_lang(es_ok, "es"))
        self.assertTrue(reply_conflicts_expected_lang(es_ok, "en"))
        # 短/弱特征不误杀
        self.assertFalse(reply_conflicts_expected_lang("Ok cariño", "es"))

    def test_reply_conflicts_en_vs_korean(self):
        ko = "갑자기 말해서 조금 놀랐어요. 천천히 이야기해 주면 좋겠어요."
        self.assertTrue(reply_conflicts_expected_lang(ko, "en"))


class DetectLangDisambiguationTest(unittest.TestCase):
    def test_japanese_kana_before_kanji(self):
        for text in (
            "好きです",
            "今日は何してるの？",
            "こんにちは、元気？",
            "おはよう",
        ):
            self.assertEqual(detect_user_message_lang(text, "zh"), "ja", text)
            self.assertEqual(detect_user_message_lang(text, "ja"), "ja", text)

    def test_chinese_stays_zh(self):
        self.assertEqual(detect_user_message_lang("你好呀，今天怎么样", "en"), "zh")

    def test_latin_under_zh_ui(self):
        self.assertEqual(detect_user_message_lang("¿Cómo me llamo?", "zh"), "es")
        self.assertEqual(detect_user_message_lang("Hola como estas amigo", "zh"), "es")
        self.assertEqual(detect_user_message_lang("Você está livre amanhã?", "zh"), "pt")
        self.assertEqual(detect_user_message_lang("Halo apa kabar kamu hari ini", "zh"), "id")
        self.assertEqual(detect_user_message_lang("Merhaba nasılsın bugün", "zh"), "tr")
        self.assertEqual(detect_user_message_lang("Hey how are you today", "zh"), "en")

    def test_short_merhaba_follows_latin_ui(self):
        self.assertEqual(detect_user_message_lang("Merhaba", "en"), "en")
        self.assertEqual(detect_user_message_lang("Merhaba", "tr"), "tr")

    def test_bulgarian_features(self):
        self.assertEqual(detect_user_message_lang("Здравей как си", "bg"), "bg")
        self.assertEqual(detect_user_message_lang("Здравей как си", "zh"), "bg")
        self.assertEqual(detect_user_message_lang("Привет как дела", "zh"), "ru")

    def test_screenshot_flow_resolve(self):
        """截图：中文 UI + 西语 → es；Habla español → sticky。"""
        r1 = resolve_reply_lang(
            current_text="¿Cómo me llamo?",
            ui_lang="zh",
            sticky="zh",
            channel="chat",
        )
        self.assertEqual(r1["reply_lang"], "es")
        self.assertEqual(r1["source"], "default")
        r2 = resolve_reply_lang(
            current_text="Habla español.",
            history_user_texts=["¿Cómo me llamo?"],
            ui_lang="zh",
            sticky=None,
            channel="chat",
        )
        self.assertEqual(r2["reply_lang"], "es")
        self.assertEqual(r2["source"], "command")
        self.assertEqual(r2["sticky"], "es")
        r3 = resolve_reply_lang(
            current_text="No tengo calor.",
            history_user_texts=["¿Cómo me llamo?", "Habla español."],
            ui_lang="zh",
            sticky="es",
            channel="chat",
        )
        self.assertEqual(r3["reply_lang"], "es")


if __name__ == "__main__":
    unittest.main()
