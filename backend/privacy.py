"""Simple privacy scanner for PII/PCI detection and redaction.

The scanner is intentionally lightweight and deterministic so it can be used
before any model/tool invocation. It detects common high-confidence sensitive
patterns and redacts them into clear placeholders.
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_check(number: str) -> bool:
    digits = [int(ch) for ch in number if ch.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False

    checksum = 0
    parity = len(digits) % 2
    for idx, digit in enumerate(digits):
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def scan_text(text: str) -> dict[str, object]:
    """Return a structured privacy scan result for the supplied text."""
    matches: list[dict[str, str | int]] = []

    for pattern, kind, replacement in (
        (EMAIL_RE, "email", "[REDACTED_EMAIL]"),
        (PHONE_RE, "phone", "[REDACTED_PHONE]"),
        (SSN_RE, "ssn", "[REDACTED_SSN]"),
    ):
        for match in pattern.finditer(text):
            matches.append({
                "type": kind,
                "match": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "replacement": replacement,
            })

    for match in CARD_RE.finditer(text):
        candidate = match.group(0).replace(" ", "").replace("-", "")
        if _luhn_check(candidate):
            matches.append({
                "type": "card",
                "match": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "replacement": "[REDACTED_CARD]",
            })

    pii_types = {entry["type"] for entry in matches if entry["type"] in {"email", "phone", "ssn"}}
    pci_found = any(entry["type"] == "card" for entry in matches)

    return {
        "has_pii": bool(pii_types),
        "has_pci": pci_found,
        "matches": matches,
    }


def redact_text(text: str) -> str:
    """Replace known PII/PCI with placeholder markers."""
    redacted = text
    ordered_rules = [
        (EMAIL_RE, "[REDACTED_EMAIL]"),
        (PHONE_RE, "[REDACTED_PHONE]"),
        (SSN_RE, "[REDACTED_SSN]"),
    ]

    for pattern, replacement in ordered_rules:
        redacted = pattern.sub(replacement, redacted)

    def _card_replacer(match: re.Match[str]) -> str:
        candidate = match.group(0).replace(" ", "").replace("-", "")
        if _luhn_check(candidate):
            return "[REDACTED_CARD]"
        return match.group(0)

    redacted = CARD_RE.sub(_card_replacer, redacted)
    return redacted
