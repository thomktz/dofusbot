"""Text normalisation shared by OCR-reading code: casefold, strip accents, tokens."""
from __future__ import annotations

import re
import unicodedata


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.casefold().replace("’", "'")).strip()


def tokens(s: str) -> set[str]:
    stop = {"le", "la", "les", "de", "des", "du", "un", "une", "a", "au", "aux", "et", "en", "the", "to", "of",
            "je", "tu", "vous", "il", "elle", "que", "qui", "pour", "avec", "sur", "dans", "l", "d", "s"}
    return {t for t in re.split(r"[^a-z0-9]+", norm(s)) if len(t) > 1 and t not in stop}
