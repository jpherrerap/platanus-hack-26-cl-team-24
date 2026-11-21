"""Baseline confidentiality for Chilean personal identifiers in the general delivery channel."""
import re
from unicodedata import normalize

from .model import Triple

# The check digit is modulo 11 with repeating weights 2..7 from the right.
# https://www.registrocivil.gob.cl/PortalOI/Manuales/Validacion_de_Run.pdf
BODY = r"(?:[0-9]{1,2}\.[0-9]{3}\.[0-9]{3}|[0-9]{7,8})"
FULL_RUT = re.compile(rf"(?<!\w)({BODY})(?:[ \t]*[-‐‑‒–—−][ \t]*)?([0-9Kk])(?!\w)")
RUT_LABEL = re.compile(r"\b(?:rut|run|rol [úu]nico (?:tributario|nacional))"
                       r"(?:\s+(?:es|n[º°.]?|n[uú]mero))?\s*[:#=]?\s*$", re.IGNORECASE)
NUMERIC_BODY = re.compile(rf"(?<!\w)({BODY})(?!\w)")
# Triple values in ISDE are identifiers. Their canonical format is rut_<body>_<dv>.
STORED_RUT = re.compile(r"\brut_([0-9]{1,8})_([0-9k])\b", re.IGNORECASE)


def valid_digit(body: str, digit: str) -> bool:
    checksum = sum(int(n) * (2 + i % 6) for i, n in enumerate(reversed(body)))
    return "0123456789K"[-checksum % 11] == digit.upper()


class RutProtection:
    def __init__(self, private_facts, recipient_records):
        self.bodies = set()
        known = [fact for _, fact, weight in recipient_records if weight > 0]
        self.known_records = {subject for subject, predicate, _ in known if predicate == "personal_rut"}
        for _, predicate, value in (*private_facts, *known):
            if predicate == "personal_rut":
                match = STORED_RUT.fullmatch(value)
                if match:
                    self.bodies.add(match[1])

    def reason(self, text: str, facts: set[Triple]) -> str | None:
        for subject, predicate, _ in facts:
            if predicate == "personal_rut":
                return "personal_identifier"
            if predicate == "identifies_person" and subject in self.known_records:
                return "personal_identifier_link"
        text = normalize("NFKC", text)
        if STORED_RUT.search(text):
            return "personal_identifier"
        for match in FULL_RUT.finditer(text):
            if valid_digit(match[1].replace(".", ""), match[2]) or RUT_LABEL.search(text[:match.start()]):
                return "personal_identifier"
        if any(match[1].replace(".", "") in self.bodies for match in NUMERIC_BODY.finditer(text)):
            return "personal_identifier"
        return None
