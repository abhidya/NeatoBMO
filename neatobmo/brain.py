"""BrainClient: the OLMoE chat seam (OpenAI-compatible HTTP).

Owns the conversation history, the request shape, and the streaming
sentence splitter, so the HTTP layer never parses SSE and routine turns
can be appended to history without knowing the wire format.
"""
import json
import re
import urllib.request

HISTORY_WINDOW = 9      # messages sent per request (persona + recent turns)


class BrainClient:
    def __init__(self, base_url, api_key=None, persona=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.history = ([{"role": "system", "content": persona}]
                        if persona else [])

    def _request(self, path, payload):
        return urllib.request.Request(
            self.base_url + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})

    def remember(self, user_text, reply):
        """Append a turn that happened outside the brain (routine layer),
        exactly as if the brain had said it — keeps its memory coherent."""
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})

    def chat(self, text):
        self.history.append({"role": "user", "content": text})
        req = self._request("/chat/completions",
                            {"model": "olmoe",
                             "messages": self.history[-HISTORY_WINDOW:]})
        with urllib.request.urlopen(req, timeout=300) as resp:
            reply = json.loads(resp.read())["choices"][0]["message"]["content"]
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def stream(self, text, on_sentence):
        """Streaming chat: emit each completed sentence while the LLM runs.

        Returns the full reply (history updated like chat()).  Raises if
        the brain doesn't answer; the caller falls back to chat().
        """
        self.history.append({"role": "user", "content": text})
        req = self._request("/chat/completions",
                            {"model": "olmoe", "stream": True,
                             "messages": self.history[-HISTORY_WINDOW:]})
        reply = ""
        emitted = 0
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw_line in resp:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                if "error" in event:
                    raise RuntimeError(event["error"])
                reply += event["choices"][0]["delta"].get("content", "")
                # flush every completed sentence in the unemitted tail
                while True:
                    match = re.search(r"[.!?;:]\s", reply[emitted:])
                    if not match:
                        break
                    end = emitted + match.end()
                    on_sentence(reply[emitted:end].strip())
                    emitted = end
        if reply[emitted:].strip():
            on_sentence(reply[emitted:].strip())
        self.history.append({"role": "assistant", "content": reply})
        return reply
