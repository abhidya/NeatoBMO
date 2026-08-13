import subprocess
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
NEATOOS = REPO / "neatoos"
SIMULATOR = NEATOOS / "build" / "neato-serial-sim"
STATE_TEST = NEATOOS / "build" / "neato-serial-state-test"
TERM = b"\x1a"


class NeatoOsSerialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.check_call(["make", "host-sim"], cwd=NEATOOS)

    def exchange(self, payload: bytes) -> bytes:
        return subprocess.check_output([str(SIMULATOR)], input=payload)

    def test_get_version_has_stock_framing_and_neatoos_identity(self):
        reply = self.exchange(b"GetVersion\n")

        self.assertEqual(reply.count(TERM), 1)
        self.assertTrue(reply.endswith(TERM))
        self.assertTrue(reply.startswith(b"GetVersion\r\nComponent,Major,Minor,Build\r\n"))
        self.assertIn(b"ModelID,-1,XV12,\r\n", reply)
        self.assertIn(b"Serial Number,NEATOOS,0000000,P\r\n", reply)
        self.assertIn(b"Software,0,1,0\r\n", reply)

    def test_crlf_is_one_command_not_an_empty_second_command(self):
        reply = self.exchange(b"GetVersion\r\n")

        self.assertEqual(reply.count(TERM), 1)

    def test_fragmented_input_and_multiple_commands(self):
        reply = self.exchange(b"getver" + b"sion\nHelp\n")

        frames = reply.split(TERM)
        self.assertEqual(len(frames), 3)
        self.assertTrue(frames[0].startswith(b"GetVersion\r\n"))
        self.assertTrue(frames[1].startswith(b"Help\r\nHelp Strlen = "))
        self.assertEqual(frames[2], b"")

    def test_help_length_matches_the_emitted_body(self):
        reply = self.exchange(b"Help\n")[:-1]
        command, length_line, body_with_blank = reply.split(b"\r\n", 2)
        body = body_with_blank[:-2]

        self.assertEqual(command, b"Help")
        self.assertEqual(length_line, f"Help Strlen = {len(body)}".encode())
        self.assertIn(b"No actuator commands are enabled.", body)

    def test_test_mode_is_stateful_but_has_no_actuator_side_effects(self):
        reply = self.exchange(b"TestMode On\nTestMode Off\n")

        self.assertEqual(
            reply,
            b"TestMode On\r\n" + TERM + b"TestMode Off\r\n" + TERM,
        )
        subprocess.check_call([str(STATE_TEST)])

    def test_provisional_unknown_command_policy_is_echo_and_terminator(self):
        self.assertEqual(
            self.exchange(b"NotACommand\n"),
            b"NotACommand\r\n" + TERM,
        )

    def test_overlong_command_is_rejected_without_echoing_truncated_input(self):
        reply = self.exchange(b"X" * 256 + b"\n")

        self.assertEqual(reply, TERM)


if __name__ == "__main__":
    unittest.main()
