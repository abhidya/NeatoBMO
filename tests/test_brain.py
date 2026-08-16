"""Brain history and ephemeral compound-turn prompt behavior."""
import json
import unittest
from unittest import mock

from neatobmo.brain import BrainClient


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class _StreamResponse(_Response):
    def __iter__(self):
        return iter(self.body)


class CompoundTurnHistoryTests(unittest.TestCase):
    @mock.patch("neatobmo.brain.urllib.request.urlopen")
    def test_chat_uses_ephemeral_prompt_but_remembers_canonical_turn(self, urlopen):
        urlopen.return_value = _Response(json.dumps({
            "choices": [{"message": {"content": "Blue light scatters!"}}]
        }).encode())
        brain = BrainClient("http://brain.test/v1", persona="Be BMO")

        generated = brain.chat(
            "what time is it and why is the sky blue?",
            prompt="Answer only: why is the sky blue? Already said: It is 10:35!",
            assistant_prefix="It is 10:35!",
        )

        self.assertEqual(generated, "Blue light scatters!")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["messages"][-1]["content"],
                         "Answer only: why is the sky blue? Already said: It is 10:35!")
        self.assertEqual(brain.history[-2], {
            "role": "user",
            "content": "what time is it and why is the sky blue?",
        })
        self.assertEqual(brain.history[-1], {
            "role": "assistant",
            "content": "It is 10:35! Blue light scatters!",
        })

    @mock.patch("neatobmo.brain.urllib.request.urlopen")
    def test_stream_commits_one_combined_history_turn(self, urlopen):
        urlopen.return_value = _StreamResponse([
            b'data: {"choices":[{"delta":{"content":"Blue light "}}]}\n',
            b'data: {"choices":[{"delta":{"content":"scatters! "}}]}\n',
            b"data: [DONE]\n",
        ])
        brain = BrainClient("http://brain.test/v1")
        sentences = []

        generated = brain.stream(
            "what time is it and why is the sky blue?",
            sentences.append,
            prompt="Answer only: why is the sky blue?",
            assistant_prefix="It is 10:35!",
        )

        self.assertEqual(generated, "Blue light scatters! ")
        self.assertEqual(sentences, ["Blue light scatters!"])
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["messages"][-1]["content"],
                         "Answer only: why is the sky blue?")
        self.assertEqual(brain.history, [
            {"role": "user",
             "content": "what time is it and why is the sky blue?"},
            {"role": "assistant",
             "content": "It is 10:35! Blue light scatters!"},
        ])

    @mock.patch("neatobmo.brain.urllib.request.urlopen")
    def test_failed_stream_does_not_commit_an_incomplete_history_turn(self,
                                                                      urlopen):
        urlopen.return_value = _StreamResponse([
            b'data: {"choices":[{"delta":{"content":"Partial answer. "}}]}\n',
            b'data: {"error":"model stopped"}\n',
        ])
        brain = BrainClient("http://brain.test/v1")
        sentences = []

        with self.assertRaisesRegex(RuntimeError, "model stopped"):
            brain.stream("explain entropy", sentences.append)

        self.assertEqual(sentences, ["Partial answer."])
        self.assertEqual(brain.history, [])


if __name__ == "__main__":
    unittest.main()
