"""WS 下发：断开连接不得当成用户打断，回复仍应落库。"""
import asyncio
import unittest

from core.ws_delivery import queue_has_user_interrupt, ws_session_key


class TestQueueHasUserInterrupt(unittest.TestCase):
    def test_disconnect_is_not_interrupt(self):
        async def run():
            q = asyncio.Queue()
            await q.put({"__disconnect": True})
            self.assertFalse(queue_has_user_interrupt(q))
            self.assertFalse(q.empty())
            item = await q.get()
            self.assertTrue(item.get("__disconnect"))

        asyncio.run(run())

    def test_user_text_is_interrupt(self):
        async def run():
            q = asyncio.Queue()
            await q.put({"text": "hello"})
            self.assertTrue(queue_has_user_interrupt(q))
            item = await q.get()
            self.assertEqual(item.get("text"), "hello")

        asyncio.run(run())

    def test_empty_queue_is_not_interrupt(self):
        async def run():
            q = asyncio.Queue()
            self.assertFalse(queue_has_user_interrupt(q))

        asyncio.run(run())

    def test_disconnect_then_user_text_is_interrupt(self):
        async def run():
            q = asyncio.Queue()
            await q.put({"__disconnect": True})
            await q.put({"text": "next"})
            self.assertTrue(queue_has_user_interrupt(q))
            first = await q.get()
            second = await q.get()
            self.assertTrue(first.get("__disconnect"))
            self.assertEqual(second.get("text"), "next")

        asyncio.run(run())


class TestWsSessionKey(unittest.TestCase):
    def test_session_key_stable(self):
        self.assertEqual(ws_session_key(12, "abc"), ws_session_key(12, "abc"))
        self.assertNotEqual(ws_session_key(12, "abc"), ws_session_key(13, "abc"))


if __name__ == "__main__":
    unittest.main()
