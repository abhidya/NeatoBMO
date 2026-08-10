import unittest

from neatobmo import emote


class FakeRobot:
    def __init__(self):
        self.sent = []

    def cmd(self, c, timeout=3.0):
        self.sent.append(c)
        return ""


class ParseEmojis(unittest.TestCase):
    def test_order_and_mapping(self):
        self.assertEqual(emote.parse_emojis("hi 😍 there 🎉!"),
                         [emote.LOVE, emote.PARTY])

    def test_cap_matches_firmware(self):
        self.assertEqual(len(emote.parse_emojis("😊" * 20)), emote.MAX_CASCADE)

    def test_heart_matches_with_variation_selector(self):
        self.assertEqual(emote.parse_emojis("❤️"), [emote.LOVE])

    def test_plain_text_has_no_faces(self):
        self.assertEqual(emote.parse_emojis("just words"), [])

    def test_every_mapped_face_is_drawable(self):
        for face in emote.EMOJI_FACES.values():
            self.assertIn(face, emote.FACES)


class Drawing(unittest.TestCase):
    def setUp(self):
        self._gap = emote.CMD_GAP
        emote.CMD_GAP = 0
        emote._shown = None

    def tearDown(self):
        emote.CMD_GAP = self._gap
        emote._shown = None

    def assert_full_span_grammar(self, sent):
        """fw 2.4: HLine/VLine take exactly one number; a stray trailing
        number is parsed as a Contrast value and written to NAND."""
        for c in sent:
            if not c.startswith("SetLCD "):
                continue
            parts = c.split()
            if parts[1] in ("HLine", "VLine", "Contrast"):
                self.assertEqual(len(parts), 3, f"extra args in {c!r}")
                self.assertTrue(parts[2].isdigit(), f"bad arg in {c!r}")
            else:
                self.assertEqual(len(parts), 2, f"extra args in {c!r}")

    def test_draw_face_carves_full_span_lines(self):
        r = FakeRobot()
        emote.draw_face(r, emote.HAPPY)
        self.assertEqual(r.sent[:2], ["SetLCD BGWhite", "SetLCD FGBlack"])
        self.assertTrue(any(" HLine " in c for c in r.sent))
        self.assertTrue(any(" VLine " in c for c in r.sent))
        self.assert_full_span_grammar(r.sent)

    def test_nested_blink_is_a_cheap_delta(self):
        r = FakeRobot()
        emote.draw_face(r, emote.HAPPY)
        full = len(r.sent)
        r.sent.clear()
        emote.draw_face(r, emote.BLINK)   # nested in happy: delta redraw
        self.assertLess(len(r.sent), full // 3)
        self.assertNotIn("SetLCD BGWhite", r.sent)
        self.assert_full_span_grammar(r.sent)

    def test_non_nested_face_recarves(self):
        r = FakeRobot()
        emote.draw_face(r, emote.BLINK)
        r.sent.clear()
        emote.draw_face(r, emote.SURPRISED)  # wider band: needs full carve
        self.assertIn("SetLCD BGWhite", r.sent)

    def test_every_face_carves_with_valid_grammar(self):
        for face in emote.FACES:
            emote._shown = None
            r = FakeRobot()
            emote.draw_face(r, face)
            self.assert_full_span_grammar(r.sent)

    def test_cascade_plain_text_smiles(self):
        r = FakeRobot()
        n = emote.cascade(r, "no emojis here")
        self.assertEqual(n, 1)
        self.assertEqual(r.sent[0], "TestMode On")
        self.assert_full_span_grammar(r.sent)


if __name__ == "__main__":
    unittest.main()
