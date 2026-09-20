"""
Phase 6+7 — Gap Analyzer & Novelty Agent
Builds a gap matrix from selected papers and derives a novelty statement.
"""

import json
import logging

from core.llm import chat
from core.state import PaperState
from core.utils import truncate

logger = logging.getLogger(__name__)


def run(state: PaperState, progress_cb=None) -> PaperState:
    state.mark_running("gap_analysis")

    papers = state.selected_papers or []
    if not papers:
        state.mark_failed("gap_analysis", "No selected papers — run /literature first")
        return state

    _cb(progress_cb, "Building gap matrix…")
    gap_md = _build_gap_matrix(papers, state.topic, state.reconstruction or "")
    state.gap_analysis = gap_md
    state.write_file("research_audit/research_gap.md", gap_md)

    _cb(progress_cb, "Deriving novelty statement…")
    novelty_md = _derive_novelty(gap_md, state.topic, state.reconstruction or "")
    state.novelty_statement = novelty_md
    state.write_file("research_audit/novelty_statement.md", novelty_md)

    # Update research questions with gap info
    if state.reconstruction:
        _cb(progress_cb, "Updating research questions…")
        updated_rq = _update_rq(state.reconstruction, gap_md, novelty_md, state.topic)
        state.reconstruction = updated_rq
        state.write_file("research_audit/research_reconstruction.md", updated_rq)

    state.mark_done("gap_analysis", state.path("research_audit/research_gap.md"))
    state.mark_done("novelty", state.path("research_audit/novelty_statement.md"))
    state.save()

    _cb(progress_cb, "✅ Gap analysis and novelty complete")
    return state


def _build_gap_matrix(papers: list[dict], topic: str, reconstruction: str) -> str:
    # Build paper summaries for the LLM
    summaries = []
    for i, p in enumerate(papers[:30]):
        summaries.append(
            f"{i+1}. {p.get('title','?')} ({p.get('year','?')}) — {p.get('journal','?')}\n"
            f"   Abstract: {p.get('abstract','')[:300]}"
        )

    prompt = f"""You are performing a systematic research gap analysis for a study on: "{topic}"

Research context from repository:
{truncate(reconstruction, 800)}

Selected papers:
{chr(10).join(summaries)}

Produce the following in markdown:

## 1. Gap Matrix

Create a markdown table:
| Study (Author, Year) | Dataset | Method | Theory | Key Finding | Limitation | Gap |

Include all {min(len(papers), 20)} most relevant papers. Be specific — no generic statements.

## 2. Gap Classification

For each gap type, write 2–4 specific sentences citing actual studies above:

### Theoretical Gap
### Methodological Gap
### Empirical Gap
### Geographical/Contextual Gap
### Dataset Gap
### Integration Gap

## 3. Gap Summary

Answer these four questions with specific, cited evidence:

**WHAT IS ALREADY KNOWN?**
**WHAT IS NOT KNOWN?**
**WHY DOES IT MATTER?**
**WHAT DOES THIS STUDY ADD?**

Rules:
- Never write "There is limited research" without evidence.
- Every gap claim must cite at least one paper from the list above.
- If a gap cannot be evidenced, write [EVIDENCE_REQUIRED].
"""
    return chat(prompt, system="You are a rigorous academic gap analysis expert.")


def _derive_novelty(gap_md: str, topic: str, reconstruction: str) -> str:
    prompt = f"""Based on the research gap analysis below, derive a conservative novelty statement for a paper on: "{topic}"

Gap analysis:
{truncate(gap_md, 2000)}

Repository research context:
{truncate(reconstruction, 600)}

Produce a markdown document:

## Novelty Statement

Write 3–5 sentences in conservative academic language stating what is novel about this study.
Only claim novelty that is directly supported by the gap analysis above.

## Novelty Categories

Mark each category as: ✅ Applies | ❌ Does not apply | ⚠️ Partial

- [ ] New dataset
- [ ] New context or geography
- [ ] New methodological combination
- [ ] New theoretical integration
- [ ] New empirical finding
- [ ] New analytical framework
- [ ] New computational approach

## Contribution Statement (for Abstract)

Write 2–3 sentences suitable for inclusion in an abstract, starting with "This study..."

Rules:
- Do NOT exaggerate novelty.
- Do NOT claim novelty not supported by evidence.
- Use phrases like "to the best of our knowledge" where appropriate.
- If novelty is weak, say so explicitly.
"""
    return chat(prompt, system="You are a conservative academic novelty evaluator.")


def _update_rq(reconstruction: str, gap_md: str, novelty_md: str, topic: str) -> str:
    prompt = f"""Update the Research Questions section of this research reconstruction document based on the gap and novelty analysis.

Original reconstruction:
{truncate(reconstruction, 3000)}

Gap analysis summary:
{truncate(gap_md, 1000)}

Novelty statement:
{truncate(novelty_md, 500)}

Return the FULL updated reconstruction document with improved, gap-grounded Research Questions.
Keep all [DATA], [CODE], [DOCUMENTATION], [DERIVED_RESULT], [LITERATURE], [ASSUMPTION], [EVIDENCE_REQUIRED] tags.
"""
    return chat(prompt, system="You are a rigorous academic research reconstructor.")


def _cb(fn, msg: str):
    if fn:
        fn(msg)
    else:
        logger.info(msg)
