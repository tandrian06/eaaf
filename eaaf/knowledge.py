import re
import unicodedata
from asyncio import Lock
from math import hypot, isfinite
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


MAX_DOCUMENT_CHARS = 4000
MIN_SEMANTIC_SCORE = 0.35


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


async def embed(client: httpx.AsyncClient, texts: list[str]) -> list[tuple[float, ...]]:
    response = await client.post(
        "/v1/embeddings", json={"model": "eaaf-knowledge-embedding", "input": texts}
    )
    response.raise_for_status()
    rows = sorted(response.json()["data"], key=lambda row: row["index"])
    if [row["index"] for row in rows] != list(range(len(texts))):
        raise ValueError("Invalid embedding indices")
    vectors = []
    for row in rows:
        vector = tuple(row["embedding"])
        norm = hypot(*vector)
        if not isfinite(norm) or norm == 0:
            raise ValueError("Invalid embedding vector")
        vectors.append(tuple(value / norm for value in vector))
    return vectors


class SemanticIndex:
    def __init__(self, corpus: dict[str, list[Source]]):
        self.corpus = corpus
        self.vectors: dict[str, list[tuple[float, ...]]] = {}
        self.locks = {department: Lock() for department in corpus}

    async def search(self, client: httpx.AsyncClient, department: str, question: str) -> list[Source]:
        sources = self.corpus[department]
        async with self.locks[department]:
            vectors = self.vectors.get(department)
            if vectors is None:
                texts = [f"{source.title}\n{source.excerpt}" for source in sources]
                query, *vectors = await embed(client, [question, *texts])
                if any(len(vector) != len(query) for vector in vectors):
                    raise ValueError("Incompatible embedding dimensions")
                self.vectors[department] = vectors
            else:
                query = None
        if query is None:
            query, = await embed(client, [question])

        ranked = sorted(
            (
                (sum(a * b for a, b in zip(query, vector, strict=True)), source)
                for vector, source in zip(vectors, sources, strict=True)
            ),
            key=lambda match: match[0],
            reverse=True,
        )
        return [source for score, source in ranked if score >= MIN_SEMANTIC_SCORE][:2]
