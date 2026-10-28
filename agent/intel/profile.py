"""Editable personality profile. Facts carry a source; only user-provided, verified facts may back a claim."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Fact(BaseModel):
    id: str
    text: str
    topic: str = "general"
    source: Literal["user", "writing_sample", "model"] = "user"
    verified: bool = True
    shareable: bool = True       # false = known to the agent but never to be mentioned


class Style(BaseModel):
    summary: str = ""
    rules: list[str] = Field(default_factory=list)
    vocabulary: list[str] = Field(default_factory=list)
    max_words: int = 30
    emoji: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    version: int = 1
    name: str
    age: int | None = None
    facts: list[Fact] = Field(default_factory=list)
    writing_samples: list[str] = Field(default_factory=list)
    style: Style = Style()
    boundaries: list[str] = Field(default_factory=list)
    needs_approval_topics: list[str] = Field(default_factory=list)
    intro_templates: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Profile":
        return cls.model_validate(json.loads(Path(path).read_text()))

    def save(self, path: Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))

    def verified_facts(self) -> list[Fact]:
        return [f for f in self.facts if f.verified and f.source == "user" and f.shareable]

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def relevant_facts(self, text: str, k: int = 6) -> list[Fact]:
        """Keyword retrieval with stopwords and prefix matching ("ski" matches "skiing"). Small profiles don't
        need embeddings, and this stays deterministic and testable."""
        def terms(t: str) -> set[str]:
            return {w for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in STOPWORDS}
        query = terms(text)
        scored = []
        for f in self.verified_facts():
            ft = terms(f"{f.text} {f.topic}")
            score = sum(1 for q in query if any(w.startswith(q) or q.startswith(w) for w in ft))
            scored.append((score, f.id, f))
        scored.sort(key=lambda x: (-x[0], x[1]))
        picked = [f for s, _, f in scored[:k] if s > 0]
        # Core identity facts (name, age, hometown) are always available so "how old are you" can be answered truthfully.
        return picked + [f for f in self.verified_facts() if f.topic == "identity" and f not in picked]


STOPWORDS = set("""the and you your are for with that this have has was were what when where who why how not but its it's
from they them their there here just like love really very much some any all can could would should will about been
being into out our ours hey hello yeah yes okay lol did does doing done get got going one day""".split())
