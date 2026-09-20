"""
Orchestrator — Full pipeline runner and slash command router.
"""

import logging
import os
from typing import Callable, Optional

from core.state import PaperState
from core.utils import slugify
import agents.repo_auditor     as repo_auditor
import agents.literature       as literature
import agents.gap_analyzer     as gap_analyzer
import agents.writer           as writer
import agents.citation_validator as citation_validator
import agents.reviewer         as reviewer
import agents.formatter        as formatter
import config

logger = logging.getLogger(__name__)

# Maps slash command names → (phase_key, handler_fn)
COMMANDS = {
    "research":           ("repo_audit",          None),        # handled inline
    "literature":         ("literature",           literature.run),
    "find-gap":           ("gap_analysis",         gap_analyzer.run),
    "check-citation":     ("citation_validation",  citation_validator.run),
    "write-introduction": ("writing",              lambda s, cb: writer.run(s, "introduction", cb)),
    "write-method":       ("writing",              lambda s, cb: writer.run(s, "methodology", cb)),
    "analyze-results":    ("writing",              lambda s, cb: writer.run(s, "results", cb)),
    "create-figures":     ("writing",              None),        # stub — figures from repo
    "create-tables":      ("writing",              None),        # stub — tables from repo
    "reviewer":           ("review",               reviewer.run),
    "plagiarism-risk":    ("review",               None),        # handled inline
    "scopus-check":       ("formatting",           None),        # handled inline
    "final-audit":        ("formatting",           None),        # handled inline
}

FULL_PIPELINE = [
    "repo_audit",
    "literature",
    "find-gap",
    "check-citation",
    "write-introduction",
    "write-method",
    "analyze-results",
    "reviewer",
    "format",
]


class Orchestrator:
    def __init__(self, progress_cb: Optional[Callable[[str], None]] = None):
        self.progress_cb = progress_cb

    def _cb(self, msg: str):
        if self.progress_cb:
            self.progress_cb(msg)
        else:
            logger.info(msg)

    # ── Full pipeline ─────────────────────────────────────────────────────────

    def run_pipeline(
        self,
        github_url:     str,
        topic:          str,
        target_journal: str = "",
        citation_style: str = "apa",
        output_format:  str = "both",
        resume:         bool = False,
    ) -> PaperState:
        """Run the full end-to-end pipeline."""
        slug = slugify(topic)
        state = PaperState.load_or_create(
            slug=slug,
            github_url=github_url,
            topic=topic,
            target_journal=target_journal,
            citation_style=citation_style,
            output_format=output_format,
        )
        state.ensure_dirs()

        self._cb(f"📂 Paper workspace: {state.workspace}")

        # Phase 1+2: Repo audit
        if not (resume and state.is_done("repo_audit")):
            self._cb("─" * 50)
            self._cb("📁 Phase 1+2: Repository Audit")
            state = repo_auditor.run(state, self.progress_cb)
            if state.phase_status.get("repo_audit") == "failed":
                self._cb("❌ Pipeline aborted: repo audit failed")
                return state
        else:
            self._cb("⏩ Skipping repo audit (already done)")

        # Phase 5: Literature
        if not (resume and state.is_done("literature")):
            self._cb("─" * 50)
            self._cb("📚 Phase 5: Literature Search")
            state = literature.run(state, self.progress_cb)
        else:
            self._cb("⏩ Skipping literature (already done)")

        # Phase 6+7: Gap + Novelty
        if not (resume and state.is_done("gap_analysis")):
            self._cb("─" * 50)
            self._cb("🔍 Phase 6+7: Gap Analysis & Novelty")
            state = gap_analyzer.run(state, self.progress_cb)
        else:
            self._cb("⏩ Skipping gap analysis (already done)")

        # Phase 9–12: Writing (all sections)
        if not (resume and state.is_done("writing")):
            self._cb("─" * 50)
            self._cb("✍️  Phase 9–12: Writing Manuscript")
            state = writer.run(state, section=None, progress_cb=self.progress_cb)
        else:
            self._cb("⏩ Skipping writing (already done)")

        # Phase 13: Citation validation
        if not (resume and state.is_done("citation_validation")):
            self._cb("─" * 50)
            self._cb("🔗 Phase 13: Citation Validation")
            state = citation_validator.run(state, self.progress_cb)
        else:
            self._cb("⏩ Skipping citation validation (already done)")

        # Phase 14: Reviewer loop
        if not (resume and state.is_done("review")):
            self._cb("─" * 50)
            self._cb("🧑‍🔬 Phase 14: Reviewer Loop")
            state = reviewer.run(state, self.progress_cb)
        else:
            self._cb("⏩ Skipping review (already done)")

        # Phase 15+16: Format
        self._cb("─" * 50)
        self._cb("📄 Phase 15+16: Formatting Output")
        state = formatter.run(state, self.progress_cb)

        # Final audit
        self._cb("─" * 50)
        self._cb("✅ Generating final audit…")
        _write_final_audit(state)

        state.save()
        self._cb("─" * 50)
        self._cb(f"🎉 Pipeline complete! Output: {state.path('final')}")
        if state.errors:
            self._cb(f"⚠️  {len(state.errors)} errors — see state.json")
        return state

    # ── Slash commands ────────────────────────────────────────────────────────

    def run_command(self, command: str, state: PaperState) -> PaperState:
        """Execute a single slash command against an existing paper state."""
        cmd = command.lstrip("/")

        if cmd == "research":
            return self._cmd_research(state)
        if cmd == "plagiarism-risk":
            return self._cmd_plagiarism_risk(state)
        if cmd == "scopus-check":
            return self._cmd_scopus_check(state)
        if cmd == "final-audit":
            _write_final_audit(state)
            state.save()
            return state
        if cmd == "create-figures":
            return self._cmd_figures(state)
        if cmd == "create-tables":
            return self._cmd_tables(state)
        if cmd == "format":
            return formatter.run(state, self.progress_cb)

        entry = COMMANDS.get(cmd)
        if entry is None:
            self._cb(f"⚠️ Unknown command: /{cmd}")
            return state

        _, handler = entry
        if handler is None:
            self._cb(f"⚠️ /{cmd} has no handler")
            return state

        try:
            return handler(state, self.progress_cb)
        except Exception as e:
            logger.error("Command /%s failed: %s", cmd, e)
            state.errors.append(f"/{cmd} error: {e}")
            state.save()
            return state

    # ── Inline command handlers ───────────────────────────────────────────────

    def _cmd_research(self, state: PaperState) -> PaperState:
        """Re-run or resume repo audit."""
        return repo_auditor.run(state, self.progress_cb)

    def _cmd_plagiarism_risk(self, state: PaperState) -> PaperState:
        from core.llm import chat
        from core.utils import truncate
        self._cb("Analyzing plagiarism risk…")

        papers_abstracts = ""
        if state.selected_papers:
            lines = []
            for p in state.selected_papers[:20]:
                lines.append(f"- {p.get('title','?')}: {p.get('abstract','')[:300]}")
            papers_abstracts = "\n".join(lines)

        manuscript_text = "\n\n".join(
            f"# {k}\n{v}" for k, v in state.manuscript.items() if v
        )

        prompt = f"""Analyze the following manuscript for plagiarism risk.

Manuscript:
{truncate(manuscript_text, 5000)}

Source abstracts from literature:
{truncate(papers_abstracts, 2000)}

For each manuscript section, identify:
1. Near-verbatim passages (>10 consecutive words matching a source)
2. Over-reliance on a single source (>40% of section content from one paper)
3. Paraphrasing that is too close to the original

For each issue:
- Section
- Risk level: HIGH | MEDIUM | LOW
- Problematic passage
- Source it resembles
- Recommended rewrite

Also give an overall risk score: HIGH | MEDIUM | LOW

Output as a markdown report.
"""
        report = chat(prompt, system="You are an academic integrity expert.")
        state.write_file("audit/plagiarism_risk.md", report)
        self._cb("✅ Plagiarism risk report written")
        return state

    def _cmd_scopus_check(self, state: PaperState) -> PaperState:
        from core.llm import chat
        from core.utils import truncate
        self._cb("Searching Scopus-indexed journal targets…")

        prompt = f"""Recommend the top 5 Scopus-indexed journals suitable for a paper on: "{state.topic}"

Research context:
{truncate(state.reconstruction or '', 800)}

For each journal provide:
| # | Journal Name | ISSN | Publisher | Scope | Citation Style | Submission URL | Notes |

Then for each journal, list:
- Word limit
- Abstract limit
- Reference style
- Special requirements

Use only real, verifiable journals. Do not invent journals.
If you are unsure about a detail, write [VERIFY].
"""
        report = chat(prompt, system="You are an academic publishing expert.")
        state.write_file("final/journal_targets.md", report)
        self._cb("✅ Journal targets written to final/journal_targets.md")
        return state

    def _cmd_figures(self, state: PaperState) -> PaperState:
        """Generate figures from validated results."""
        from core.llm import chat
        from core.utils import truncate
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import os

        self._cb("Generating figures from validated results…")
        figs_dir = state.path("figures")
        os.makedirs(figs_dir, exist_ok=True)

        validated = state.read_file("analysis/validated_results.md")
        if not validated:
            self._cb("⚠️ No validated results found — skipping figure generation")
            return state

        prompt = f"""Based on the validated results below, describe 3–5 figures suitable for a paper on: "{state.topic}"

Validated results:
{truncate(validated, 2000)}

For each figure return JSON:
{{
  "figures": [
    {{
      "number": 1,
      "title": "...",
      "type": "bar|line|scatter|heatmap|network",
      "x_label": "...",
      "y_label": "...",
      "data": {{"labels": [...], "values": [...]}},
      "caption": "..."
    }}
  ]
}}

Only describe figures that can be generated from the data above. Return ONLY JSON.
"""
        try:
            import json, re
            raw = chat(prompt, system="You are a data visualization expert.")
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if not m:
                self._cb("⚠️ Could not parse figure specs")
                return state
            specs = json.loads(m.group())["figures"]

            for spec in specs:
                fig, ax = plt.subplots(figsize=(8, 5))
                data = spec.get("data", {})
                labels = data.get("labels", [])
                values = data.get("values", [])
                ftype = spec.get("type", "bar")

                if ftype == "bar" and labels and values:
                    ax.bar(labels, values)
                elif ftype == "line" and labels and values:
                    ax.plot(labels, values, marker="o")
                elif ftype == "scatter" and labels and values:
                    ax.scatter(range(len(values)), values)
                else:
                    ax.text(0.5, 0.5, f"[Figure {spec['number']}]\n{spec['title']}",
                            ha="center", va="center", transform=ax.transAxes)

                ax.set_title(spec.get("title", ""))
                ax.set_xlabel(spec.get("x_label", ""))
                ax.set_ylabel(spec.get("y_label", ""))
                plt.tight_layout()

                fname = f"fig_{spec['number']:02d}.png"
                fpath = os.path.join(figs_dir, fname)
                plt.savefig(fpath, dpi=150, bbox_inches="tight")
                plt.close()

                # Caption file
                state.write_file(
                    f"figures/fig_{spec['number']:02d}_caption.md",
                    f"**Figure {spec['number']}.** {spec.get('caption', spec.get('title', ''))}"
                )
                self._cb(f"  ✅ {fname}")

        except Exception as e:
            logger.error("Figure generation failed: %s", e)
            state.warnings.append(f"Figure generation failed: {e}")

        return state

    def _cmd_tables(self, state: PaperState) -> PaperState:
        from core.llm import chat
        from core.utils import truncate
        self._cb("Generating tables from validated results…")

        validated = state.read_file("analysis/validated_results.md")
        if not validated:
            self._cb("⚠️ No validated results found — skipping table generation")
            return state

        prompt = f"""Based on the validated results below, generate 2–4 tables suitable for a paper on: "{state.topic}"

Validated results:
{truncate(validated, 2000)}

For each table produce:
1. A markdown table with a descriptive title
2. A LaTeX version using booktabs

Label as Table 1, Table 2, etc.
Only include data present in the validated results.
Write [EVIDENCE_REQUIRED] for any missing data.
"""
        tables_text = chat(prompt, system="You are an academic data presentation expert.")
        state.write_file("tables/tables.md", tables_text)
        self._cb("✅ Tables written to tables/tables.md")
        return state


# ── Final audit ────────────────────────────────────────────────────────────────

def _write_final_audit(state: PaperState):
    checks = [
        ("All manuscript sections present",
         all(state.manuscript.get(s) for s in ["abstract", "introduction", "methodology", "results", "discussion", "conclusion"])),
        ("Literature retrieved",
         bool(state.selected_papers)),
        ("Gap analysis done",
         bool(state.gap_analysis)),
        ("Citation audit done",
         bool(state.citation_audit)),
        ("No HALLUCINATED citations",
         "HALLUCINATED" not in (state.citation_audit or "")),
        ("Reviewer report done",
         bool(state.reviewer_reports)),
        ("No EVIDENCE_REQUIRED in abstract",
         "EVIDENCE_REQUIRED" not in (state.manuscript.get("abstract", ""))),
        ("No EVIDENCE_REQUIRED in results",
         "EVIDENCE_REQUIRED" not in (state.manuscript.get("results", ""))),
        ("DOCX or LaTeX generated",
         os.path.exists(state.path("final/manuscript_final.docx")) or
         os.path.exists(state.path("final/manuscript_final.tex"))),
    ]

    lines = [
        "# Final Pre-Submission Audit\n",
        f"**Paper:** {state.topic}  ",
        f"**Workspace:** {state.workspace}  \n",
        "## Checklist\n",
        "| Check | Status |",
        "|-------|--------|",
    ]
    for label, passed in checks:
        status = "✅ Pass" if passed else "❌ Fail"
        lines.append(f"| {label} | {status} |")

    fail_count = sum(1 for _, p in checks if not p)
    lines += [
        "",
        f"**Result:** {len(checks) - fail_count}/{len(checks)} checks passed",
    ]
    if fail_count == 0:
        lines.append("\n✅ **Manuscript is ready for submission.**")
    else:
        lines.append(f"\n⚠️ **{fail_count} check(s) failed — review before submission.**")

    if state.errors:
        lines += ["\n## Errors\n"]
        for e in state.errors:
            lines.append(f"- {e}")

    if state.warnings:
        lines += ["\n## Warnings\n"]
        for w in state.warnings:
            lines.append(f"- {w}")

    audit_text = "\n".join(lines)
    state.write_file("final/final_audit.md", audit_text)
