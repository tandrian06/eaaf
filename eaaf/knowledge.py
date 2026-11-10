import re
import unicodedata
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    keywords: list[str]
    excerpt: str


def load_knowledge(path: Path) -> dict[str, list[Source]]:
    return TypeAdapter(dict[str, list[Source]]).validate_python(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def words(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return set(re.findall(r"\w+", without_accents))


def retrieve(sources: list[Source], question: str) -> list[Source]:
    terms = words(question)
    matches = [
        (sum(words(keyword) <= terms for keyword in source.keywords), source)
        for source in sources
    ]
    ranked = sorted(matches, key=lambda match: match[0], reverse=True)
    return [source for score, source in ranked if score > 0][:2]
