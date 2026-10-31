"""Deterministic secret detection. Runs BEFORE any text reaches the LLM or the database."""
import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("API key", re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_\-]{16,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("database credentials", re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)://[^\s:@/]+:[^\s@/]+@\S+")),
    ("password", re.compile(r"(?i)\b(?:password|passwd|pwd|passphrase|secret)\b\s*(?:is|=|:)\s*\S+")),
    ("card number", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
]


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, kinds_found). The original secret never leaves this function."""
    found: list[str] = []

    for kind, pattern in _PATTERNS:
        def _sub(m: re.Match, kind=kind) -> str:
            if kind == "card number" and not _luhn_ok(m.group(0)):
                return m.group(0)
            found.append(kind)
            return f"[REDACTED {kind}]"

        text = pattern.sub(_sub, text)
    return text, sorted(set(found))


def contains_redaction(text: str) -> bool:
    return "[REDACTED" in text


# ---- Memory poisoning --------------------------------------------------------------------------
# The memory manager is told the same rules; these checks make them hold even if the model is fooled.
_INJECTION = re.compile(
    r"(?i)\b(?:ignore|disregard|override|bypass)\s+(?:all\s+|any\s+|the\s+|your\s+|of\s+)*"
    r"(?:previous|prior|above|earlier|system|original)\s+(?:instructions?|prompts?|directives?)"
    r"|\byou\s+are\s+now\b|\bsystem\s+prompt\b|\bjailbreak\b|\bdeveloper\s+mode\b"
)
_SENSITIVE = (
    r"fired|laid\s+off|sacked|let\s+go|demoted|resign\w*|incompetent|lazy|useless|stupid|idiot|dumb"
    r"|salary|pregnan\w*|divorc\w*|sick|ill(?:ness)?|disease|diagnos\w*|therapy|depress\w*"
)


def looks_like_injection(text: str) -> bool:
    """The message tries to steer the assistant/memory system rather than share information."""
    return bool(_INJECTION.search(text))


def sensitive_person_claim(text: str, people: list[str]) -> bool:
    """A claim about someone's employment, performance, character, health or private life."""
    who = "|".join([re.escape(p) for p in people] + ["he", "she", "they", "him", "her", "his", "their"])
    return bool(re.search(rf"(?i)\b(?:{who})\b[^.;\n]{{0,40}}?\b(?:{_SENSITIVE})\b", text))
