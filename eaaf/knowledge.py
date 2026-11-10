import re
import unicodedata
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


MAX_DOCUMENT_CHARS = 4000


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    keywords: list[str] = Field(min_length=1)


class Document(SourceMetadata):
    file: Path


class Source(SourceMetadata):
    excerpt: str


def load_knowledge(path: Path) -> dict[str, list[Source]]:
    manifest = TypeAdapter(dict[str, list[Document]]).validate_python(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    root = (path.parent.parent / "knowledge").resolve()
    corpus = {}
    for space, documents in manifest.items():
        department = root / space
        if Path(space).name != space or department.resolve() != department:
            raise ValueError(f"Invalid knowledge directory for {space}")
        sources = []
        seen = set()
        for document in documents:
            if document.id in seen:
                raise ValueError(f"Duplicate knowledge source in {space}: {document.id}")
            seen.add(document.id)
            source_path = (department / document.file).resolve()
            if (
                document.file.is_absolute()
                or not source_path.is_relative_to(department)
                or source_path.suffix.lower() != ".md"
            ):
                raise ValueError(f"Knowledge source must be Markdown within {space}: {document.id}")
            with source_path.open(encoding="utf-8") as source_file:
                text = source_file.read(MAX_DOCUMENT_CHARS + 1)
            if len(text) > MAX_DOCUMENT_CHARS:
                raise ValueError(f"Knowledge source is too long: {document.id}")
            heading, _, remainder = text.strip().partition("\n")
            excerpt = remainder.strip() if heading.startswith("# ") else text.strip()
            if not excerpt:
                raise ValueError(f"Knowledge source is empty: {document.id}")
            sources.append(Source(**document.model_dump(exclude={"file"}), excerpt=excerpt))
        corpus[space] = sources
    return corpus


def words(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return set(re.findall(r"\w+", without_accents))


def retrieve(sources: list[Source], question: str) -> list[Source]:
    terms = words(question)
    matches = [
        (sum(bool(tokens) and tokens <= terms for tokens in map(words, source.keywords)), source)
        for source in sources
    ]
    ranked = sorted(matches, key=lambda match: match[0], reverse=True)
    return [source for score, source in ranked if score > 0][:2]
