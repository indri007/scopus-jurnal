"""Shared utilities."""

import os
import re
import hashlib
import logging
import unicodedata

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Convert a topic string to a safe folder name."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60]


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def short_hash(text: str, length: int = 8) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:length]


def extract_json_block(text: str) -> str:
    """Extract the first ```json ... ``` block from LLM output."""
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # fallback: try raw JSON object/array
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_markdown_section(text: str, heading: str) -> str:
    """Extract content under a specific markdown heading."""
    pattern = rf"#{1,3}\s*{re.escape(heading)}\s*\n(.*?)(?=\n#{1,3}\s|\Z)"
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def word_count(text: str) -> int:
    return len(text.split())


def truncate(text: str, max_chars: int = 2000) -> str:
    return text[:max_chars] + "…" if len(text) > max_chars else text


def clean_doi(doi: str) -> str:
    """Normalize a DOI string."""
    doi = doi.strip()
    doi = re.sub(r"^https?://doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi


def format_authors_apa(authors: list[str], year: str) -> str:
    """Format author list in APA style: Last, F. M., & Last, F. M. (year)."""
    if not authors:
        return f"Unknown Author ({year})"
    parts = []
    for a in authors:
        parts.append(a)
    if len(parts) == 1:
        return f"{parts[0]} ({year})"
    return ", ".join(parts[:-1]) + f", & {parts[-1]} ({year})"
