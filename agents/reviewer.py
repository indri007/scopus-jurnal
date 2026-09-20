"""
Phase 14 — Reviewer Agent
Scores manuscript on 15 dimensions and drives the revision loop.
"""

import json
import logging
import re
from typing import Optional

from core.llm import chat
from core.state import PaperState
from core.utils import truncate
import config

logger = logging.getLogger(__name__)

DIMENSIONS = [
    ("A", "Research question clarity"),
    ("B", "Literature depth"),
    ("C", "Research gap specificity"),
    ("D", "Novelty evidence"),
    ("E", "Theoretical contribution"),
    ("F", "Methodological rigor"),
    ("G", "Data quality"),
    ("H", "Statistical validity"),
    ("I", "Reproducibility"),
    ("J", "Results clarity"),
    ("K", "Discussion depth"),
    ("L", "Limitations stated"),
    ("M", "Ethical considerations"),
    ("N", "Citation integrity"),
    ("O", "English academic style"),
]


def run(state: PaperState, progress_cb=None) -> PaperState:
    """Run reviewer loop until thresholds met or max rounds reached."""
    max_rounds = config.MAX_REVIEW_ROUNDS
    threshold  = config.REVIEW_PASS_THRESHOLD

    for round_num in range(1, max_rounds + 1):
        _cb(progress_cb, f"Review round {round_num}/{max_rounds}…")
        state.mark_running("review")

        review = _review_manuscript(state, round_num)
        state.reviewer_reports.append(review)
        state.review_round = round_num
        state.write_file(f"audit/reviewer_report_round_{round_num}.md", review)

        scores = _parse_scores(review)
        _cb(progress_cb, f"  Scores: avg={_avg(scores):.1f}/10 | threshold={threshold}")

        critical = _extract_issues(review, "CRITICAL")
        major    = _extract_issues(review, "MAJOR")

        _cb(progress_cb, f"  Issues: {len(critical)} critical, {len(major)} major")

        # Check pass conditions
        if (not critical and len(major) <= 2 and
                _citation_score(scores) >= threshold and
                round_num >= 1):
            _cb(progress_cb, f"✅ Review passed on round {round_num}")
            break

        # Revise if not the last round
        if round_num < max_rounds:
            _cb(progress_cb, f"  Revising manuscript based on feedback…")
            _revise_manuscript(state, review, critical + major, progress_cb)
        else:
            _cb(progress_cb, f"⚠️ Max review rounds reached — outstanding issues remain")

    # Write final combined report
    final_report = _combine_reports(state.reviewer_reports)
    state.write_file("audit/reviewer_report.md", final_report)

    state.mark_done("review", state.path("audit/reviewer_report.md"))
    state.save()
    return state


def _review_manuscript(state: PaperState, round_num: int) -> str:
    manuscript_text = _assemble_manuscript_text(state)
    citation_audit  = truncate(state.citation_audit or "", 1000)

    prompt = f"""You are an independent peer reviewer for a Scopus Q1 journal.

Paper topic: {state.topic}
Target journal: {state.target_journal or "Scopus Q1"}
Review round: {round_num}

Full manuscript:
{truncate(manuscript_text, 6000)}

Citation audit summary:
{citation_audit}

## Your Task

Score each dimension from 0–10 (10 = excellent):

{chr(10).join(f'{code}. {name}: [SCORE]/10' for code, name in DIMENSIONS)}

For each score below 7, provide:
- **Severity**: CRITICAL | MAJOR | MINOR
- **Issue**: specific description
- **Evidence**: quote the problematic text
- **Recommendation**: specific revision instruction

## Output Format

### Scores
| Code | Dimension | Score |
|------|-----------|-------|
{chr(10).join(f'| {code} | {name} | [SCORE]/10 |' for code, name in DIMENSIONS)}

### CRITICAL Issues
(Issues that prevent publication — must be fixed)

### MAJOR Issues
(Significant weaknesses — strongly recommended to fix)

### MINOR Issues
(Suggestions for improvement)

### Missing Evidence
List every [EVIDENCE_REQUIRED] tag found in the manuscript.

### Unsupported Claims
List any claims made without citation or evidence.

### Overall Assessment
Write 3–5 sentences summarising the manuscript's current state and main priorities.
"""
    return chat(prompt, system=(
        "You are a rigorous, fair academic peer reviewer. "
        "You do not give fake positive scores. "
        "You identify real problems with specific evidence. "
        "You never approve fabricated data or hallucinated citations."
    ))


def _revise_manuscript(state: PaperState, review: str, issues: list[str], progress_cb=None):
    """Ask the writer to revise each flagged section."""
    # Identify which sections need revision
    sections_to_revise = _identify_sections_to_revise(review, state.manuscript)

    for section in sections_to_revise:
        _cb(progress_cb, f"  Revising section: {section}…")
        original = state.manuscript.get(section, "")
        if not original:
            continue

        issues_text = "\n".join(f"- {issue}" for issue in issues[:10])
        relevant_review = _extract_section_feedback(review, section)

        prompt = f"""Revise the following manuscript section based on reviewer feedback.

Section: {section}
Topic: {state.topic}

Reviewer feedback relevant to this section:
{relevant_review}

Issues to address:
{issues_text}

Original section text:
{truncate(original, 3000)}

Verified references available:
{_papers_summary(state)}

Rules:
- Address each CRITICAL and MAJOR issue.
- Do NOT fabricate new data or citations.
- Do NOT remove [EVIDENCE_REQUIRED] markers — they must stay until evidence is provided.
- If a citation was flagged as HALLUCINATED in the citation audit, remove it.
- Improve academic writing quality.
- Return the COMPLETE revised section text only.
"""
        try:
            revised = chat(prompt, system=(
                "You are a rigorous academic writer revising a manuscript for a Scopus Q1 journal. "
                "Never fabricate data or citations."
            ))
            state.manuscript[section] = revised
            state.write_file(f"manuscript/{section}.md", revised)
        except Exception as e:
            logger.error("Failed to revise section %s: %s", section, e)


def _identify_sections_to_revise(review: str, manuscript: dict) -> list[str]:
    """Find which sections are mentioned in the review issues."""
    section_keywords = {
        "introduction": ["introduction", "intro"],
        "literature_review": ["literature", "review"],
        "theoretical_framework": ["theoretical", "framework", "theory"],
        "methodology": ["methodology", "method", "data collection", "sampling"],
        "results": ["results", "findings", "table", "figure"],
        "discussion": ["discussion", "interpretation"],
        "limitations": ["limitations", "limitation"],
        "conclusion": ["conclusion"],
        "abstract": ["abstract"],
    }
    review_lower = review.lower()
    to_revise = []
    for section, keywords in section_keywords.items():
        if section in manuscript and any(kw in review_lower for kw in keywords):
            to_revise.append(section)
    return to_revise or list(manuscript.keys())


def _extract_section_feedback(review: str, section: str) -> str:
    """Extract review paragraphs mentioning a section."""
    lines = review.split("\n")
    relevant = []
    section_words = section.replace("_", " ").lower()
    for line in lines:
        if section_words in line.lower() or any(
            w in line.lower() for w in section_words.split()
        ):
            relevant.append(line)
    return "\n".join(relevant) or review[:800]


def _parse_scores(review: str) -> dict[str, float]:
    scores = {}
    pattern = r'\|\s*([A-O])\s*\|[^|]+\|\s*(\d+(?:\.\d+)?)/10\s*\|'
    for m in re.finditer(pattern, review):
        scores[m.group(1)] = float(m.group(2))
    return scores


def _avg(scores: dict) -> float:
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


def _citation_score(scores: dict) -> float:
    return scores.get("N", 0.0)


def _extract_issues(review: str, severity: str) -> list[str]:
    issues = []
    in_section = False
    for line in review.split("\n"):
        if f"### {severity}" in line:
            in_section = True
            continue
        if in_section and line.startswith("###"):
            in_section = False
        if in_section and line.strip().startswith("-"):
            issues.append(line.strip())
    return issues


def _assemble_manuscript_text(state: PaperState) -> str:
    order = [
        "abstract", "introduction", "literature_review", "theoretical_framework",
        "methodology", "results", "discussion",
        "theoretical_implications", "practical_implications",
        "limitations", "conclusion"
    ]
    parts = []
    for sec in order:
        text = state.manuscript.get(sec, "")
        if text:
            parts.append(f"# {sec.replace('_', ' ').title()}\n\n{text}")
    return "\n\n---\n\n".join(parts)


def _papers_summary(state: PaperState) -> str:
    if not state.selected_papers:
        return "(no verified references)"
    lines = []
    for p in state.selected_papers[:20]:
        lines.append(f"- {'; '.join(p.get('authors',[])[:2])} ({p.get('year','?')}). {p.get('title','?')}. DOI:{p.get('doi','N/A')}")
    return "\n".join(lines)


def _combine_reports(reports: list[str]) -> str:
    lines = ["# Reviewer Reports — All Rounds\n"]
    for i, report in enumerate(reports, 1):
        lines.append(f"---\n## Round {i}\n")
        lines.append(report)
    return "\n\n".join(lines)


def _cb(fn, msg: str):
    if fn:
        fn(msg)
    else:
        logger.info(msg)
