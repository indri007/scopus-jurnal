"""
Phase 13 — Citation Validator
Checks every citation in the manuscript against CrossRef.
Tags: VERIFIED | UNVERIFIED | HALLUCINATED | MISMATCH
"""

import json
import logging
import re
import time
from typing import Optional

import requests

from core.llm import chat
from core.state import PaperState
from core.utils import clean_doi, truncate
import config

logger = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"
HEADERS = {"User-Agent": "ScopusPaperGenerator/1.0 (mailto:research@jurnal.ai)"}

STATUS_VERIFIED     = "VERIFIED"
STATUS_UNVERIFIED   = "UNVERIFIED"
STATUS_HALLUCINATED = "HALLUCINATED"
STATUS_MISMATCH     = "MISMATCH"


def run(state: PaperState, progress_cb=None) -> PaperState:
    state.mark_running("citation_validation")

    _cb(progress_cb, "Extracting citations from manuscript…")
    all_text = "\n\n".join(state.manuscript.values())
    citations = _extract_citations(all_text, state.selected_papers or [])

    _cb(progress_cb, f"Validating {len(citations)} citations via CrossRef…")
    results = []
    for i, cit in enumerate(citations):
        _cb(progress_cb, f"  Checking {i+1}/{len(citations)}: {cit.get('title','?')[:60]}…")
        validated = _validate_one(cit)
        results.append(validated)
        time.sleep(0.3)  # be polite to CrossRef

    # Generate audit report
    audit_md = _build_audit_report(results, state.topic)
    state.citation_audit = audit_md
    state.write_file("audit/citation_audit.md", audit_md)

    # Generate clean .bib
    bib_content = _generate_bib(results)
    state.write_file("manuscript/references.bib", bib_content)

    # Stats
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in [STATUS_VERIFIED, STATUS_UNVERIFIED, STATUS_HALLUCINATED, STATUS_MISMATCH]}

    state.mark_done("citation_validation", state.path("audit/citation_audit.md"))
    state.save()

    _cb(progress_cb, (
        f"✅ Citation validation complete — "
        f"✅{counts[STATUS_VERIFIED]} verified, "
        f"⚠️{counts[STATUS_UNVERIFIED]} unverified, "
        f"❌{counts[STATUS_HALLUCINATED]} hallucinated, "
        f"⚠️{counts[STATUS_MISMATCH]} mismatch"
    ))
    return state


# ── Citation extraction ───────────────────────────────────────────────────────

def _extract_citations(text: str, selected_papers: list[dict]) -> list[dict]:
    """
    Build citation list from selected_papers (already verified via API)
    plus any additional inline citations found in the manuscript text.
    """
    citations = []
    seen_dois = set()

    # Primary source: selected_papers (these were retrieved from real APIs)
    for p in selected_papers:
        doi = clean_doi(p.get("doi", ""))
        if doi and doi not in seen_dois:
            seen_dois.add(doi)
            citations.append({
                "title":   p.get("title", ""),
                "authors": p.get("authors", []),
                "year":    p.get("year", ""),
                "journal": p.get("journal", ""),
                "doi":     doi,
                "source":  p.get("source", "api"),
            })

    # Secondary: extract any additional Author (Year) patterns from text
    # and try to match against selected_papers
    inline_refs = re.findall(r'\(([A-Z][a-zA-Z\-]+(?:\s+et\s+al\.?)?,?\s+\d{4}[a-z]?)\)', text)
    for ref in inline_refs:
        year_m = re.search(r'(\d{4})', ref)
        if not year_m:
            continue
        year = year_m.group(1)
        author = re.sub(r',?\s*\d{4}[a-z]?', '', ref).strip()
        # Try to match against selected papers
        matched = _fuzzy_match_paper(author, year, selected_papers)
        if matched:
            doi = clean_doi(matched.get("doi", ""))
            if doi and doi not in seen_dois:
                seen_dois.add(doi)
                citations.append({
                    "title":   matched.get("title", ""),
                    "authors": matched.get("authors", []),
                    "year":    year,
                    "journal": matched.get("journal", ""),
                    "doi":     doi,
                    "source":  "inline_match",
                })
        else:
            # Unknown citation — flag for hallucination check
            if f"unknown_{author}_{year}" not in seen_dois:
                seen_dois.add(f"unknown_{author}_{year}")
                citations.append({
                    "title":   f"[Unknown: {author} {year}]",
                    "authors": [author],
                    "year":    year,
                    "journal": "",
                    "doi":     "",
                    "source":  "inline_unmatched",
                })

    return citations


def _fuzzy_match_paper(author: str, year: str, papers: list[dict]) -> Optional[dict]:
    """Try to match an inline citation to a paper in the list."""
    author_clean = author.replace("et al.", "").strip().lower()
    for p in papers:
        if p.get("year", "") != year:
            continue
        paper_authors = " ".join(p.get("authors", [])).lower()
        if author_clean and author_clean[:5] in paper_authors:
            return p
    return None


# ── CrossRef validation ───────────────────────────────────────────────────────

def _validate_one(cit: dict) -> dict:
    result = dict(cit)
    doi = cit.get("doi", "").strip()

    if not doi:
        result["status"] = STATUS_HALLUCINATED
        result["note"] = "No DOI — cannot verify"
        return result

    try:
        url = f"{CROSSREF_URL}/{doi}"
        r = requests.get(url, headers=HEADERS, timeout=int(config.CROSSREF_TIMEOUT))
        if r.status_code == 404:
            result["status"] = STATUS_HALLUCINATED
            result["note"] = f"DOI not found in CrossRef: {doi}"
            return result
        r.raise_for_status()
        data = r.json().get("message", {})

        # Check title match
        cr_titles = data.get("title", [])
        cr_title = cr_titles[0].lower() if cr_titles else ""
        our_title = cit.get("title", "").lower()

        if cr_title and our_title and not _title_match(our_title, cr_title):
            result["status"] = STATUS_MISMATCH
            result["note"] = f"Title mismatch — CrossRef: '{cr_titles[0][:80]}'"
            result["crossref_title"] = cr_titles[0]
        else:
            result["status"] = STATUS_VERIFIED
            result["note"] = "DOI resolves and title matches"
            # Update with CrossRef data
            if cr_titles:
                result["title"] = cr_titles[0]
            ct = data.get("container-title", [])
            if ct:
                result["journal"] = ct[0]
            dp = data.get("published", {}).get("date-parts", [[None]])
            if dp and dp[0][0]:
                result["year"] = str(dp[0][0])

    except requests.Timeout:
        result["status"] = STATUS_UNVERIFIED
        result["note"] = "CrossRef timeout"
    except Exception as e:
        result["status"] = STATUS_UNVERIFIED
        result["note"] = f"Verification error: {e}"

    return result


def _title_match(a: str, b: str, threshold: float = 0.6) -> bool:
    """Simple word-overlap title match."""
    words_a = set(re.findall(r'\w+', a.lower()))
    words_b = set(re.findall(r'\w+', b.lower()))
    if not words_a or not words_b:
        return True  # can't compare
    overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
    return overlap >= threshold


# ── Audit report ─────────────────────────────────────────────────────────────

def _build_audit_report(results: list[dict], topic: str) -> str:
    lines = [
        f"# Citation Audit Report\n",
        f"**Topic:** {topic}  ",
        f"**Total citations checked:** {len(results)}  \n",
    ]

    # Summary table
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    lines += [
        "## Summary\n",
        "| Status | Count |",
        "|--------|-------|",
        f"| ✅ VERIFIED | {counts.get('VERIFIED', 0)} |",
        f"| ⚠️ UNVERIFIED | {counts.get('UNVERIFIED', 0)} |",
        f"| ❌ HALLUCINATED | {counts.get('HALLUCINATED', 0)} |",
        f"| ⚠️ MISMATCH | {counts.get('MISMATCH', 0)} |",
        "",
    ]

    # Detail table
    lines += [
        "## Detail\n",
        "| # | Title | Authors | Year | DOI | Status | Note |",
        "|---|-------|---------|------|-----|--------|------|",
    ]
    for i, r in enumerate(results, 1):
        authors = "; ".join(r.get("authors", [])[:2])
        doi = r.get("doi", "N/A")
        doi_link = f"[{doi}](https://doi.org/{doi})" if doi and doi != "N/A" else "N/A"
        title = r.get("title", "?")[:60]
        status = r.get("status", "?")
        note = r.get("note", "")[:80]
        lines.append(f"| {i} | {title} | {authors} | {r.get('year','?')} | {doi_link} | {status} | {note} |")

    # Issues section
    issues = [r for r in results if r["status"] in (STATUS_HALLUCINATED, STATUS_MISMATCH)]
    if issues:
        lines += ["", "## Issues Requiring Attention\n"]
        for r in issues:
            lines.append(f"- **{r['status']}**: {r.get('title','?')[:80]} — {r.get('note','')}")

    return "\n".join(lines)


# ── BibTeX generation ─────────────────────────────────────────────────────────

def _generate_bib(results: list[dict]) -> str:
    entries = []
    for r in results:
        if r.get("status") == STATUS_HALLUCINATED:
            continue  # never include hallucinated refs in .bib
        doi = r.get("doi", "")
        authors = r.get("authors", [])
        year = r.get("year", "0000")
        title = r.get("title", "Unknown")
        journal = r.get("journal", "")

        # Build cite key: FirstAuthorLastNameYearFirstWord
        first_author = (authors[0].split(",")[0] if authors else "Unknown").strip()
        first_word = re.sub(r'[^a-zA-Z]', '', title.split()[0]) if title.split() else "X"
        cite_key = f"{first_author}{year}{first_word}"
        cite_key = re.sub(r'[^a-zA-Z0-9]', '', cite_key)

        author_bib = " and ".join(authors) if authors else "Unknown"
        doi_field = f"  doi = {{{doi}}},\n" if doi else ""

        entries.append(
            f"@article{{{cite_key},\n"
            f"  author  = {{{author_bib}}},\n"
            f"  title   = {{{title}}},\n"
            f"  journal = {{{journal}}},\n"
            f"  year    = {{{year}}},\n"
            f"{doi_field}"
            f"}}"
        )

    return "\n\n".join(entries)


def _cb(fn, msg: str):
    if fn:
        fn(msg)
    else:
        logger.info(msg)
