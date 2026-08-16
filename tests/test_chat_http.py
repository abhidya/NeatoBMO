"""HTTP contracts for incremental compound chat and thinking audio."""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

import bmo_web
from neatobmo.body import BodyController
from neatobmo.routines import ConvoState


class _RoutineBrain:
    def __init__(self):
        self.remembered = []

    def remember(self, user, reply):
        self.remembered.append((user, reply))

    def chat(self, *_args, **_kwargs):
        raise AssertionError("routine-only request reached the Brain")


class ChatHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), bmo_web.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.saved = (bmo_web.brain, bmo_web.body, bmo_web.convo_state)
        bmo_web.brain = _RoutineBrain()
        bmo_web.body = BodyController()
        bmo_web.convo_state = ConvoState()

    def tearDown(self):
        bmo_web.brain, bmo_web.body, bmo_web.convo_state = self.saved

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1",
                                          self.server.server_address[1],
                                          timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        return conn, conn.getresponse()

    def test_chat_stream_returns_ordered_ndjson_events(self):
        payload = json.dumps({
            "text": "what time is it and check your battery",
            "speak": False,
        })
        conn, response = self.request(
            "POST", "/chat", payload,
            {"Content-Type": "application/json",
             "Accept": "application/x-ndjson"})
        try:
            events = [json.loads(line) for line in response]
        finally:
            conn.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"),
                         "application/x-ndjson")
        self.assertEqual([event["type"] for event in events], [
            "turn_started", "routine_result", "routine_result",
            "turn_completed",
        ])
        self.assertEqual([event["seq"] for event in events], list(range(4)))
        self.assertFalse(events[-1]["brain_used"])

    def test_brain_sentence_flushes_before_generation_finishes(self):
        release = threading.Event()

        class StreamingBrain:
            def stream(self, text, on_sentence, **kwargs):
                on_sentence("First answer! [happy]")
                if not release.wait(2):
                    raise RuntimeError("test did not release generation")
                on_sentence("Second answer! [party]")
                return "First answer! [happy] Second answer! [party]"

        bmo_web.brain = StreamingBrain()
        payload = json.dumps({
            "text": "what time is it and explain the sky",
            "speak": False,
        })
        conn, response = self.request(
            "POST", "/chat", payload,
            {"Content-Type": "application/json",
             "Accept": "application/x-ndjson"})
        try:
            first_events = [json.loads(response.readline()) for _ in range(4)]
            self.assertEqual([event["type"] for event in first_events], [
                "turn_started", "routine_result", "brain_started",
                "brain_result",
            ])
            self.assertEqual(first_events[-1]["display"], "First answer! 😀")
            release.set()
            remaining = [json.loads(line) for line in response]
        finally:
            release.set()
            conn.close()

        self.assertEqual([event["type"] for event in remaining],
                         ["brain_result", "turn_completed"])

    def test_thinking_sound_route_is_allowlisted(self):
        conn, response = self.request("GET", "/thinking-sound?name=blip-a")
        try:
            wav = response.read()
        finally:
            conn.close()
        self.assertEqual(response.getheader("Content-Type"), "audio/wav")
        self.assertTrue(wav.startswith(b"RIFF"))

        conn, response = self.request(
            "GET", "/thinking-sound?name=../../providers")
        try:
            rejected = json.loads(response.read())
        finally:
            conn.close()
        self.assertEqual(rejected["error"], "unknown thinking sound")

    def test_voice_catalog_and_clip_routes_expose_the_runtime_library(self):
        conn, response = self.request("GET", "/voice/catalog?q=hello%20there")
        try:
            catalog = json.loads(response.read())
        finally:
            conn.close()
        self.assertGreaterEqual(catalog["count"], 200)
        self.assertEqual(catalog["trusted_count"], 214)
        self.assertEqual(catalog["quarantined_count"], 16)
        self.assertEqual(catalog["pending_review_count"], 14)
        self.assertEqual(catalog["rejected_count"], 2)
        self.assertEqual(catalog["mode"], "soundboard")
        self.assertIsNotNone(catalog["exact"])

        conn, response = self.request(
            "GET", "/voice/clip?text=hello%20there")
        try:
            wav = response.read()
        finally:
            conn.close()
        self.assertEqual(response.getheader("Content-Type"), "audio/wav")
        self.assertTrue(wav.startswith(b"RIFF"))


if __name__ == "__main__":
    unittest.main()
