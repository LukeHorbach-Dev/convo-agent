"""Last pass on model-written replies so the punctuation matches how Luke types (from his own voice guide):
a space before every question mark, no apostrophes in contractions (im, dont, thats), a lowercase "i" mid
sentence, no exclamation marks, no em-dashes, semicolons or colons, and no emoji (":)" is his and stays).

Only wording the model wrote goes through this. Text Luke types or edits himself is sent exactly as he wrote it.
"""
from __future__ import annotations

import re

_CONTRACTION = re.compile(r"\b([A-Za-z]+)['’](m|s|t|re|ve|ll|d)\b")
_MID_I = re.compile(r"(?<=[a-z,] )I\b")                       # "and I" -> "and i"; a sentence-initial I stays
_KEEP = {("we", "ll"), ("we", "re"), ("he", "ll"), ("she", "ll"), ("i", "ll")}
_SMILEY = ":)"
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def in_lukes_voice(text: str) -> str:
    t = text.replace(_SMILEY, "\x00")                         # protect his smiley from the colon rule
    t = _EMOJI.sub("", t)
    # we'll/we're/he'll/she'd... would turn into other words (well, were, hell, shed), so those keep the apostrophe
    t = _CONTRACTION.sub(lambda m: m.group(0) if (m.group(1).lower(), m.group(2)) in _KEEP or
                         (m.group(1).lower() in ("i", "we", "he", "she") and m.group(2) == "d") else m.group(1) + m.group(2), t)
    t = re.sub(r"\bIm\b", "im", t)                            # "Im" reads odd; he writes "im"
    t = re.sub(r"\bIve\b", "ive", t)
    t = _MID_I.sub("i", t)
    t = t.replace("!", ".")
    t = re.sub(r"\s*[—–;]\s*", ", ", t)
    t = re.sub(r"(?<!\d):(?!\d)", ",", t)                     # keep times like 6:30
    t = re.sub(r"\s*\?", " ?", t)                             # "going?" -> "going ?"
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    t = re.sub(r"\.\s+\.", ".", t)
    return t.replace("\x00", _SMILEY).strip()
