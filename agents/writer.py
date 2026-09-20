"""
Phase 9+10+11 — Writer Agent
Writes all IMRaD manuscript sections grounded in evidence and verified citations.
"""

import logging
from typing import Optional

from core.llm import chat
from core.state import PaperState
from core.utils import truncate

logger = logging.getLogger(__name__)

SECTIONS = [
    "abstract",
    "introduction",
    "literature_review",
    "theoretical_framework",
    "methodology",
    "results",
    "discussion",
    "theoretical_implications",
    "practical_implications",
    "limitations",
    "conclusion",
]


def run(state: PaperState, section: Optional[str] = None, progress_cb=None) -> PaperState:
    """Write one section or all sections."""
    state.mark_running("writing")
    targets = [section] if section else SECTIONS

    for sec in targets:
        _cb(progress_cb, f"Writing {sec.replace('_', ' ').title()}…")
        try:
            text = _write_section(sec, state)
            state.manuscript[sec] = text
            state.write_file(f"manuscript/{sec}.md", text)
        except Exception as e:
            logger.error("Failed to write section %s: %s", sec, e)
            state.manuscript[sec] = f"[EVIDENCE_REQUIRED: section writing failed — {e}]"

    state.mark_done("writing", state.path("manuscript"))
    state.save()
    _cb(progress_cb, f"✅ Writing complete ({len(targets)} sections)")
    return state


def _context(state: PaperState) -> dict:
    """Build shared context dict passed to all section writers."""
    papers_bib = ""
    if state.selected_papers:
        lines = []
        for p in state.selected_papers[:40]:
            authors = "; ".join(p.get("authors", [])[:3])
            lines.append(
                f"- {authors} ({p.get('year','?')}). {p.get('title','?')}. "
                f"{p.get('journal','?')}. DOI:{p.get('doi','N/A')}"
            )
        papers_bib = "\n".join(lines)

    return {
        "topic":           state.topic,
        "journal":         state.target_journal or "a Scopus Q1 journal",
        "citation_style":  state.citation_style,
        "reconstruction":  truncate(state.reconstruction or "", 2000),
        "gap":             truncate(state.gap_analysis or "", 1000),
        "novelty":         truncate(state.novelty_statement or "", 600),
        "papers_bib":      papers_bib,
        "validated":       truncate(state.read_file("analysis/validated_results.md"), 1500),
    }


def _write_section(section: str, state: PaperState) -> str:
    ctx = _context(state)
    fn = _SECTION_FN.get(section)
    if fn is None:
        return f"[EVIDENCE_REQUIRED: no writer defined for section '{section}']"
    return fn(ctx, state)


# ── Section writers ───────────────────────────────────────────────────────────

def _write_abstract(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write a structured academic abstract for a paper on: "{ctx['topic']}"

Target journal: {ctx['journal']}
Citation style: {ctx['citation_style']}

Research context:
{ctx['reconstruction']}

Novelty:
{ctx['novelty']}

Key results (if available):
{ctx['validated']}

The abstract must follow this structure (do not use subheadings, write as a single paragraph or structured block):
- Background/Purpose (1–2 sentences)
- Methods (2–3 sentences)
- Results (2–3 sentences — use ONLY validated results, write [EVIDENCE_REQUIRED] if absent)
- Conclusion/Implications (1–2 sentences)

Word limit: 200–250 words.
Do NOT fabricate results. Do NOT use citations in the abstract.
"""
    return chat(prompt, system=_sys())


def _write_introduction(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write the Introduction section for an academic paper on: "{ctx['topic']}"

Target: {ctx['journal']} | Citation style: {ctx['citation_style']}

Verified references (use ONLY these for in-text citations):
{ctx['papers_bib']}

Research reconstruction:
{ctx['reconstruction']}

Gap analysis:
{ctx['gap']}

Novelty:
{ctx['novelty']}

Structure the introduction with these 8 paragraphs:
P1: Broad research problem (cite 2–3 papers)
P2: Why the problem matters (cite evidence)
P3: Current scholarly knowledge (cite 3–4 papers)
P4: What previous studies have done (cite specific studies)
P5: What remains unresolved
P6: Specific research gap (link to gap analysis)
P7: How this study addresses the gap
P8: Contribution statement

End with: "This study addresses the following research questions: ..."

Rules:
- Every literature claim MUST cite a paper from the verified reference list above.
- Use Author (Year) format for {ctx['citation_style']}.
- Do NOT cite papers not in the verified list.
- Do NOT fabricate findings.
- Target: 600–900 words.
"""
    return chat(prompt, system=_sys())


def _write_literature_review(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write the Literature Review section for a paper on: "{ctx['topic']}"

Verified references:
{ctx['papers_bib']}

Gap analysis:
{ctx['gap']}

Research context:
{ctx['reconstruction']}

Organise the review thematically (not chronologically).
Each theme should:
1. Introduce the theme
2. Synthesise 3–5 papers (not just summarise each separately)
3. Note agreements and contradictions
4. Connect to this study's research gap

Use {ctx['citation_style']} in-text citations.
Target: 800–1200 words.
Only cite papers from the verified reference list.
"""
    return chat(prompt, system=_sys())


def _write_theoretical_framework(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write the Theoretical Framework section for a paper on: "{ctx['topic']}"

Research context:
{ctx['reconstruction']}

Verified references:
{ctx['papers_bib']}

The section must:
1. Identify the core theory/theories underpinning this study
2. Define key theoretical constructs
3. Explain how the theory applies to this study's context
4. Present a conceptual model or framework diagram description (in text)

Use {ctx['citation_style']} citations. Target: 400–600 words.
If the theoretical framework cannot be determined from available evidence, write [EVIDENCE_REQUIRED: theoretical framework unclear from repository].
"""
    return chat(prompt, system=_sys())


def _write_methodology(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write the Methodology section for a paper on: "{ctx['topic']}"

Research reconstruction (contains actual methods used):
{ctx['reconstruction']}

Validated results context:
{ctx['validated']}

The section MUST cover (use subheadings):
### Research Design
### Data Source and Collection
### Sampling and Inclusion Criteria
### Data Preprocessing
### Analytical Framework
### Variables / Features
### Algorithms and Tools (with versions where known)
### Validation Strategy
### Ethical Considerations

Rules:
- Write ONLY what can be evidenced from the repository.
- For missing information, write: [EVIDENCE_REQUIRED: specify what is missing]
- Do NOT invent hyperparameters, sample sizes, or tool versions.
- Use passive voice as appropriate for academic writing.
- Target: 800–1000 words.
"""
    return chat(prompt, system=_sys())


def _write_results(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write the Results section for a paper on: "{ctx['topic']}"

VALIDATED RESULTS (use ONLY these — do not fabricate):
{ctx['validated']}

Research context:
{ctx['reconstruction']}

Rules:
- Report findings objectively. NO interpretation here.
- Reference tables and figures by number: "Table 1 shows...", "As illustrated in Figure 1..."
- Every numerical value must come from the validated results above.
- If a result is absent: write [EVIDENCE_REQUIRED: describe the missing result]
- Use subheadings for each result category.
- Target: 600–900 words.
"""
    return chat(prompt, system=_sys())


def _write_discussion(ctx: dict, state: PaperState) -> str:
    results_text = state.manuscript.get("results", "")
    prompt = f"""Write the Discussion section for a paper on: "{ctx['topic']}"

Results:
{truncate(results_text, 1500)}

Verified references (for comparison with literature):
{ctx['papers_bib']}

Gap analysis:
{ctx['gap']}

For each major finding, follow this structure:
1. State the finding
2. Compare with previous literature (cite verified papers)
3. Explain agreement or disagreement
4. Propose a possible mechanism
5. Connect to theory
6. State the contribution
7. Note limitations of interpretation

Rules:
- Do NOT simply repeat the Results section.
- Avoid causal claims unless the study design supports causality.
- Use hedging language: "may suggest", "appears to indicate", etc.
- Target: 800–1000 words.
"""
    return chat(prompt, system=_sys())


def _write_implications(ctx: dict, state: PaperState) -> str:
    discussion = state.manuscript.get("discussion", "")
    prompt = f"""Write the Theoretical and Practical Implications for a paper on: "{ctx['topic']}"

Discussion summary:
{truncate(discussion, 1000)}

Novelty:
{ctx['novelty']}

## Theoretical Implications
(3–4 paragraphs: what does this add to theory, what frameworks does it extend)

## Practical Implications
(3–4 paragraphs: what should practitioners, policymakers, or managers do differently)

Target: 400–600 words total.
"""
    return chat(prompt, system=_sys())


def _write_limitations(ctx: dict, state: PaperState) -> str:
    prompt = f"""Write the Limitations section for a paper on: "{ctx['topic']}"

Research context:
{ctx['reconstruction']}

The section must honestly acknowledge:
- Dataset limitations
- Methodological limitations
- Generalisability constraints
- Temporal scope
- Any [EVIDENCE_REQUIRED] items from the methodology

Also briefly state how future research could address each limitation.
Target: 250–350 words.
"""
    return chat(prompt, system=_sys())


def _write_conclusion(ctx: dict, state: PaperState) -> str:
    abstract = state.manuscript.get("abstract", "")
    prompt = f"""Write the Conclusion section for a paper on: "{ctx['topic']}"

Abstract (for alignment):
{abstract}

Novelty:
{ctx['novelty']}

The conclusion must:
1. Restate the research problem
2. Summarise the main findings (do not introduce new results)
3. State the contribution to knowledge
4. Suggest future research directions

Target: 250–350 words. Do NOT use subheadings.
"""
    return chat(prompt, system=_sys())


def _sys() -> str:
    return (
        "You are a rigorous academic writer producing a manuscript for a Scopus Q1 journal. "
        "Every claim must be evidence-based. Never fabricate data, citations, or results. "
        "If evidence is missing, write [EVIDENCE_REQUIRED] with a specific description."
    )


_SECTION_FN = {
    "abstract":               _write_abstract,
    "introduction":           _write_introduction,
    "literature_review":      _write_literature_review,
    "theoretical_framework":  _write_theoretical_framework,
    "methodology":            _write_methodology,
    "results":                _write_results,
    "discussion":             _write_discussion,
    "theoretical_implications": lambda ctx, s: _write_implications(ctx, s),
    "practical_implications": lambda ctx, s: _write_implications(ctx, s),
    "limitations":            _write_limitations,
    "conclusion":             _write_conclusion,
}


def _cb(fn, msg: str):
    if fn:
        fn(msg)
    else:
        logger.info(msg)
