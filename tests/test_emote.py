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

    def tearDown(self):
        emote.CMD_GAP = self._gap

    def test_draw_face_clears_then_draws_segments(self):
        r = FakeRobot()
        emote.draw_face(r, emote.HAPPY)
        self.assertEqual(r.sent[:2], ["SetLCD BGWhite", "SetLCD FGBlack"])
        self.assertTrue(all(c.startswith("SetLCD ") for c in r.sent))
        self.assertTrue(any("HLine" in c or "VLine" in c for c in r.sent[2:]))

    def test_rect_uses_fewest_segments(self):
        r = FakeRobot()
        emote._rect(r, (10, 20, 12, 40))   # tall box -> vertical segments
        self.assertTrue(all(c.startswith("SetLCD VLine ") for c in r.sent))
        self.assertEqual(len(r.sent), 3)

    def test_cascade_plain_text_smiles(self):
        r = FakeRobot()
        n = emote.cascade(r, "no emojis here")
        self.assertEqual(n, 1)
        self.assertEqual(r.sent[0], "TestMode On")


if __name__ == "__main__":
    unittest.main()
