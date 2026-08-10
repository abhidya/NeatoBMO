import io
import struct
import types
import unittest
import wave
from unittest import mock

import bmo_brain_server
from neatobmo.esp32 import Esp32Client
from neatobmo.robot import Robot
from neatobmo.sounds import LIVE_SOUND_IDS, SOUNDS
from neatobmo.transport import BinaryTransferError, SerialTransport


class FakeSerial:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.reset_count = 0
        self.flushed = False

    def reset_input_buffer(self):
        self.reset_count += 1

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, _size):
        return self.replies.pop(0) if self.replies else b""

    def flush(self):
        self.flushed = True


class BinaryTransferTests(unittest.TestCase):
    def transport(self, replies):
        transport = SerialTransport.__new__(SerialTransport)
        transport.ser = FakeSerial(replies)
        return transport

    def test_neato_framing_matches_updater_protocol(self):
        transport = self.transport([b"PlaySound File\r\n\x05", b"\x06"])
        payload = b"RIFF" + bytes(range(32))

        transport.send_binary("PlaySound File", payload, timeout=0.1)

        self.assertEqual(
            transport.ser.writes,
            [
                f"PlaySound File Size {len(payload) + 4}\r".encode(),
                payload,
                struct.pack("<I", sum(payload)),
            ],
        )
        self.assertTrue(transport.ser.flushed)

    def test_command_terminator_before_enq_is_a_clear_error(self):
        transport = self.transport([b"PlaySound File\r\nUnknown option\r\n\x1a"])

        with self.assertRaisesRegex(BinaryTransferError, "did not request binary data"):
            transport.send_binary("PlaySound File", b"payload", timeout=0.1)

    def test_nak_rejects_payload(self):
        transport = self.transport([b"\x05", b"\x15"])

        with self.assertRaisesRegex(BinaryTransferError, "NAK"):
            transport.send_binary("PlaySound File", b"payload", timeout=0.1)

    def test_binary_error_preserves_terminal_text(self):
        transport = self.transport([b"\x05", b"Bad sound module\r\n\x1a"])

        with self.assertRaisesRegex(BinaryTransferError, "Bad sound module"):
            transport.send_binary("Upload sound noburn", b"payload", timeout=0.1)

    def test_no_burn_transfer_allows_terminator_completion(self):
        transport = self.transport([b"\x05", b"\x1a"])

        reply = transport.send_binary(
            "Upload sound noburn", b"payload", timeout=0.1, allow_terminator=True
        )

        self.assertEqual(reply, b"\x1a")


class RobotVoiceTests(unittest.TestCase):
    def test_runtime_sound_vocabulary_is_limited_to_live_verified_ids(self):
        self.assertEqual(set(SOUNDS.values()), LIVE_SOUND_IDS)
        self.assertNotIn("thank_you", SOUNDS)

    def test_play_file_keeps_stock_sound_api_separate(self):
        wav = b"RIFF" + bytes(4) + b"WAVE" + bytes(32)
        robot = Robot.__new__(Robot)
        robot.t = mock.Mock()

        robot.play_file(wav)

        robot.t.send_binary.assert_called_once_with("PlaySound File", wav)

    def test_play_file_rejects_non_wav(self):
        robot = Robot.__new__(Robot)
        robot.t = mock.Mock()

        with self.assertRaisesRegex(ValueError, "WAV"):
            robot.play_file(b"not audio")

    def test_play_file_rejects_oversized_wav(self):
        robot = Robot.__new__(Robot)
        robot.t = mock.Mock()

        with self.assertRaisesRegex(ValueError, "512 KiB"):
            robot.play_file(b"RIFF" + bytes(4) + b"WAVE" + bytes(512 * 1024))


class ColibriTtsTests(unittest.TestCase):
    def setUp(self):
        self.previous_args = bmo_brain_server.ARGS
        bmo_brain_server.ARGS = types.SimpleNamespace(
            tts_engine="espeak-ng", tts_voice="en+f4", tts_speed=160, tts_pitch=70
        )

    def tearDown(self):
        bmo_brain_server.ARGS = self.previous_args

    @mock.patch("bmo_brain_server.subprocess.run")
    def test_speech_is_generated_on_the_colibri_server(self, run):
        raw = io.BytesIO()
        with wave.open(raw, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x01\x00\x02\x00")
        run.return_value = types.SimpleNamespace(
            returncode=0, stdout=raw.getvalue(), stderr=b""
        )

        wav = bmo_brain_server.synthesize_speech("Hello, friend!")

        self.assertTrue(wav.startswith(b"RIFF"))
        with wave.open(io.BytesIO(wav), "rb") as parsed:
            self.assertEqual(parsed.getnframes(), 2)
            self.assertEqual(parsed.getframerate(), 22050)
        run.assert_called_once_with(
            ["espeak-ng", "-v", "en+f4", "-s", "160", "-p", "70",
             "--stdout", "Hello, friend!"],
            capture_output=True,
            timeout=30,
            check=False,
        )


class Esp32VoiceRelayTests(unittest.TestCase):
    @mock.patch("neatobmo.esp32.urllib.request.urlopen")
    def test_posts_wav_to_esp32_native_speaker_endpoint(self, urlopen):
        response = mock.MagicMock()
        response.read.return_value = b"OK\n"
        urlopen.return_value.__enter__.return_value = response
        wav = b"RIFF" + bytes(4) + b"WAVE" + bytes(32)
        client = Esp32Client("http://10.0.0.106")

        client.speak(wav)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://10.0.0.106/speak")
        self.assertEqual(request.data, wav)
        self.assertEqual(request.headers["Content-type"], "audio/wav")


if __name__ == "__main__":
    unittest.main()
