"""
Phase 5 — Literature Agent
Queries CrossRef, OpenAlex, and Semantic Scholar for relevant papers.
Deduplicates by DOI and ranks by relevance.
"""

import json
import logging
import time
from typing import Optional

import requests

from core.llm import chat
from core.state import PaperState
from core.utils import clean_doi, truncate
import config

logger = logging.getLogger(__name__)

CROSSREF_URL   = "https://api.crossref.org/works"
OPENALEX_URL   = "https://api.openalex.org/works"
SEMSCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

HEADERS_BASE = {"User-Agent": "ScopusPaperGenerator/1.0 (mailto:research@jurnal.ai)"}


# ── Public entry point ────────────────────────────────────────────────────────

def run(state: PaperState, progress_cb=None) -> PaperState:
    state.mark_running("literature")

    keywords = _extract_keywords(state.topic, state.reconstruction or "")
    _cb(progress_cb, f"Searching with keywords: {', '.join(keywords[:5])}")

    papers = []

    _cb(progress_cb, "Querying CrossRef…")
    papers += _search_crossref(keywords, config.MAX_LITERATURE)

    _cb(progress_cb, "Querying OpenAlex…")
    papers += _search_openalex(keywords, config.MAX_LITERATURE)

    _cb(progress_cb, "Querying Semantic Scholar…")
    papers += _search_semantic_scholar(keywords, config.MAX_LITERATURE)

    _cb(progress_cb, f"Deduplicating {len(papers)} results…")
    papers = _deduplicate(papers)

    _cb(progress_cb, "Ranking by relevance…")
    selected = _rank_and_select(papers, state.topic, state.reconstruction or "", config.MAX_LITERATURE)

    # Save raw results
    raw_path = state.write_file(
        "literature/search_results.json",
        json.dumps(papers, indent=2, ensure_ascii=False)
    )

    # Save selected
    selected_path = state.write_file(
        "literature/selected_papers.json",
        json.dumps(selected, indent=2, ensure_ascii=False)
    )

    # Save CSV for citation use
    csv_content = _to_csv(selected)
    state.write_file("literature/references_verified.csv", csv_content)

    state.selected_papers = selected
    state.mark_done("literature", selected_path)
    state.save()

    _cb(progress_cb, f"✅ Literature search complete — {len(selected)} papers selected")
    return state


# ── Keyword extraction ────────────────────────────────────────────────────────

def _extract_keywords(topic: str, reconstruction: str) -> list[str]:
    prompt = f"""Extract 8–12 precise academic search keywords from this research topic and context.

Topic: {topic}
Context: {truncate(reconstruction, 800)}

Return a JSON array of keyword strings. Example:
["social network analysis", "sarcasm detection", "Indonesian Twitter", ...]

Return ONLY the JSON array, no explanation."""
    try:
        raw = chat(prompt, system="You are an academic keyword extractor.")
        import json, re
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning("Keyword extraction failed: %s", e)
    # Fallback: split topic
    return [w.strip() for w in topic.replace(",", " ").split() if len(w) > 3][:8]


# ── CrossRef ─────────────────────────────────────────────────────────────────

def _search_crossref(keywords: list[str], n: int) -> list[dict]:
    results = []
    query = " ".join(keywords[:6])
    try:
        params = {"query": query, "rows": n, "select": "DOI,title,author,published,abstract,container-title,URL"}
        r = requests.get(CROSSREF_URL, params=params, headers=HEADERS_BASE, timeout=15)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        for item in items:
            results.append(_parse_crossref(item))
    except Exception as e:
        logger.warning("CrossRef search failed: %s", e)
    return [p for p in results if p]


def _parse_crossref(item: dict) -> Optional[dict]:
    try:
        doi = clean_doi(item.get("DOI", ""))
        title_list = item.get("title", [])
        title = title_list[0] if title_list else ""
        if not title:
            return None
        authors = []
        for a in item.get("author", [])[:6]:
            name = f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            if name:
                authors.append(name)
        date_parts = item.get("published", {}).get("date-parts", [[None]])
        year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""
        journal = ""
        ct = item.get("container-title", [])
        if ct:
            journal = ct[0]
        abstract = item.get("abstract", "")
        return {
            "source":   "crossref",
            "doi":      doi,
            "title":    title,
            "authors":  authors,
            "year":     year,
            "journal":  journal,
            "abstract": abstract[:600],
            "url":      f"https://doi.org/{doi}" if doi else item.get("URL", ""),
            "verified": bool(doi),
        }
    except Exception:
        return None


# ── OpenAlex ──────────────────────────────────────────────────────────────────

def _search_openalex(keywords: list[str], n: int) -> list[dict]:
    results = []
    query = " ".join(keywords[:6])
    try:
        params = {
            "search": query,
            "per-page": n,
            "select": "doi,title,authorships,publication_year,primary_location,abstract_inverted_index",
        }
        r = requests.get(OPENALEX_URL, params=params, headers=HEADERS_BASE, timeout=15)
        r.raise_for_status()
        items = r.json().get("results", [])
        for item in items:
            parsed = _parse_openalex(item)
            if parsed:
                results.append(parsed)
    except Exception as e:
        logger.warning("OpenAlex search failed: %s", e)
    return results


def _parse_openalex(item: dict) -> Optional[dict]:
    try:
        doi = clean_doi(item.get("doi") or "")
        title = item.get("title", "")
        if not title:
            return None
        authors = []
        for a in item.get("authorships", [])[:6]:
            name = a.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)
        year = str(item.get("publication_year") or "")
        journal = ""
        loc = item.get("primary_location") or {}
        src = loc.get("source") or {}
        journal = src.get("display_name", "")
        # Reconstruct abstract from inverted index
        abstract = _invert_abstract(item.get("abstract_inverted_index") or {})
        return {
            "source":   "openalex",
            "doi":      doi,
            "title":    title,
            "authors":  authors,
            "year":     year,
            "journal":  journal,
            "abstract": abstract[:600],
            "url":      f"https://doi.org/{doi}" if doi else "",
            "verified": bool(doi),
        }
    except Exception:
        return None


def _invert_abstract(inverted: dict) -> str:
    if not inverted:
        return ""
    try:
        max_pos = max(pos for positions in inverted.values() for pos in positions)
        words = [""] * (max_pos + 1)
        for word, positions in inverted.items():
            for p in positions:
                words[p] = word
        return " ".join(w for w in words if w)
    except Exception:
        return ""


# ── Semantic Scholar ──────────────────────────────────────────────────────────

def _search_semantic_scholar(keywords: list[str], n: int) -> list[dict]:
    results = []
    query = " ".join(keywords[:6])
    headers = dict(HEADERS_BASE)
    if config.SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = config.SEMANTIC_SCHOLAR_API_KEY
    try:
        params = {
            "query": query,
            "limit": n,
            "fields": "title,authors,year,venue,externalIds,abstract,url",
        }
        r = requests.get(SEMSCHOLAR_URL, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        items = r.json().get("data", [])
        for item in items:
            parsed = _parse_semscholar(item)
            if parsed:
                results.append(parsed)
    except Exception as e:
        logger.warning("Semantic Scholar search failed: %s", e)
    return results


def _parse_semscholar(item: dict) -> Optional[dict]:
    try:
        title = item.get("title", "")
        if not title:
            return None
        ext_ids = item.get("externalIds") or {}
        doi = clean_doi(ext_ids.get("DOI", ""))
        authors = [a.get("name", "") for a in item.get("authors", [])[:6] if a.get("name")]
        year = str(item.get("year") or "")
        journal = item.get("venue", "")
        abstract = item.get("abstract") or ""
        return {
            "source":   "semantic_scholar",
            "doi":      doi,
            "title":    title,
            "authors":  authors,
            "year":     year,
            "journal":  journal,
            "abstract": abstract[:600],
            "url":      item.get("url", f"https://doi.org/{doi}" if doi else ""),
            "verified": bool(doi),
        }
    except Exception:
        return None


# ── Deduplication ─────────────────────────────────────────────────────────────

def _deduplicate(papers: list[dict]) -> list[dict]:
    seen_dois = set()
    seen_titles = set()
    unique = []
    for p in papers:
        doi = p.get("doi", "").strip().lower()
        title_key = p.get("title", "").strip().lower()[:80]
        if doi and doi in seen_dois:
            continue
        if title_key and title_key in seen_titles:
            continue
        if doi:
            seen_dois.add(doi)
        if title_key:
            seen_titles.add(title_key)
        unique.append(p)
    return unique


# ── Relevance ranking ─────────────────────────────────────────────────────────

def _rank_and_select(papers: list[dict], topic: str, reconstruction: str, n: int) -> list[dict]:
    if not papers:
        return []

    # Build compact paper list for LLM
    lines = []
    for i, p in enumerate(papers):
        lines.append(f"{i}: {p['title']} ({p['year']}) — {p['journal']} — DOI:{p['doi']}")
        if p.get("abstract"):
            lines.append(f"   Abstract: {p['abstract'][:200]}")

    prompt = f"""You are selecting the most relevant academic papers for a study on: "{topic}"

Research context:
{truncate(reconstruction, 600)}

Paper list:
{chr(10).join(lines[:100])}

Select the {min(n, len(papers))} most relevant papers.
Return ONLY a JSON array of integer indices from the list above.
Example: [0, 3, 7, 12, ...]
"""
    try:
        raw = chat(prompt, system="You are an academic literature curation expert.")
        import re
        m = re.search(r'\[[\d,\s]+\]', raw)
        if m:
            indices = json.loads(m.group())
            selected = [papers[i] for i in indices if 0 <= i < len(papers)]
            return selected[:n]
    except Exception as e:
        logger.warning("Ranking failed, returning top-%d: %s", n, e)

    return papers[:n]


# ── CSV export ────────────────────────────────────────────────────────────────

def _to_csv(papers: list[dict]) -> str:
    lines = ["title,authors,year,journal,doi,url,abstract,source,verified"]
    for p in papers:
        def esc(s): return f'"{str(s).replace(chr(34), chr(39))}"'
        lines.append(",".join([
            esc(p.get("title", "")),
            esc("; ".join(p.get("authors", []))),
            esc(p.get("year", "")),
            esc(p.get("journal", "")),
            esc(p.get("doi", "")),
            esc(p.get("url", "")),
            esc(p.get("abstract", "")[:200]),
            esc(p.get("source", "")),
            esc(p.get("verified", False)),
        ]))
    return "\n".join(lines)


def _cb(fn, msg: str):
    if fn:
        fn(msg)
    else:
        logger.info(msg)
