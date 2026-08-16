"""Pure planning for one spoken conversation turn."""
from dataclasses import dataclass
import re

from neatobmo import routines


@dataclass(frozen=True)
class RequestPart:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class RoutineStep:
    part: RequestPart
    routine: str


@dataclass(frozen=True)
class TurnPlan:
    original: str
    routines: tuple[RoutineStep, ...]
    residual_parts: tuple[RequestPart, ...]

    @property
    def requires_brain(self):
        return bool(self.residual_parts)

    @property
    def residual(self):
        return " ".join(part.text for part in self.residual_parts).strip()


_WAKE_RE = re.compile(
    r"^\s*(?:(?:hey|okay|ok|yo)\s+)?(?:b[e]{1,2}[\s-]?mo|bmo)"
    r"(?:[,!.\s]+|$)",
    re.I,
)
_PUNCT_OR_SEQUENCE_RE = re.compile(
    r"\s*(?:[,.!?;:]+|\b(?:and then|after that|also|then)\b)\s+",
    re.I,
)
_AND_RE = re.compile(r"\s+\band\b\s+", re.I)
_FILLER_EDGE_RE = re.compile(
    r"^(?:please|can you|could you|would you|will you|and|then|also)\s+|"
    r"\s+(?:please)$",
    re.I,
)
_MEANINGLESS_RE = re.compile(
    r"^(?:please|thanks?|thank you|and|then|also|hey|okay|ok|yo|"
    r"b[e]{1,2}[\s-]?mo|bmo|[,!?.;\s]+)*$",
    re.I,
)


def _clean_text(text):
    out = re.sub(r"\s+", " ", text.strip(" \t\r\n,.;:!?"))
    while True:
        cleaned = _FILLER_EDGE_RE.sub("", out).strip(" \t\r\n,.;:!?")
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned == out:
            return cleaned
        out = cleaned


def _meaningful(text):
    return bool(text.strip()) and _MEANINGLESS_RE.fullmatch(text.strip()) is None


def _strip_wake(text):
    match = _WAKE_RE.match(text)
    if not match:
        return text, 0
    return text[match.end():], match.end()


def _split_with_offsets(text, base, pattern):
    out = []
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            part = text[cursor:match.start()]
            out.append(RequestPart(_clean_text(part),
                                   base + cursor, base + match.start()))
        cursor = match.end()
    if cursor < len(text):
        out.append(RequestPart(_clean_text(text[cursor:]),
                               base + cursor, base + len(text)))
    return tuple(part for part in out if part.text)


def _split_parts(text, base, state=None):
    strong = _split_with_offsets(text, base, _PUNCT_OR_SEQUENCE_RE)
    if not strong:
        strong = (RequestPart(_clean_text(text), base, base + len(text)),)

    parts = []
    for part in strong:
        split = _split_with_offsets(part.text, part.start, _AND_RE)
        if any(routines.accepted_routine(p.text, state) for p in split):
            parts.extend(split)
        else:
            parts.append(part)
    return tuple(parts)


def plan_turn(text, state=None):
    """Plan local routine steps and residual request text without side effects."""
    original = text or ""
    body, base = _strip_wake(original)
    parts = _split_parts(body, base, state)
    routines_out = []
    residual = []

    for part in parts:
        if not _meaningful(part.text):
            continue
        routine = routines.accepted_routine(part.text, state)
        if routine:
            routines_out.append(RoutineStep(part, routine))
        else:
            residual.append(part)

    if not routines_out and residual:
        cleaned = _clean_text(body)
        if _meaningful(cleaned):
            residual = [RequestPart(cleaned, base, base + len(body))]

    return TurnPlan(original=original,
                    routines=tuple(routines_out),
                    residual_parts=tuple(residual))
