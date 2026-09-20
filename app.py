"""
Scopus Q1 Paper Generator — Streamlit UI
"""

import os
import sys
import time
import threading
from queue import Queue, Empty

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Scopus Q1 Paper Generator",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Load API keys from Streamlit secrets ──────────────────────────────────────
def _load_secrets():
    """Inject Streamlit secrets into environment so config.py picks them up."""
    try:
        secrets = st.secrets
        for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
                    "SEMANTIC_SCHOLAR_API_KEY", "SCOPUS_API_KEY",
                    "GITHUB_TOKEN", "LLM_PROVIDER", "LLM_MODEL"]:
            val = secrets.get(key, "")
            if val:
                os.environ[key] = val
    except Exception:
        pass  # local dev — use .env file

_load_secrets()

import config
from core.state import PaperState
from core.utils import slugify
from agents.orchestrator import Orchestrator


# ── Session state helpers ─────────────────────────────────────────────────────
def _init_session():
    defaults = {
        "paper_state":    None,
        "log_lines":      [],
        "running":        False,
        "pipeline_done":  False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📄 Paper Generator")
    st.caption("Scopus Q1 — Evidence-based pipeline")
    st.divider()

    github_url = st.text_input(
        "GitHub Repository URL",
        placeholder="https://github.com/user/repo",
        help="The research repository to transform into a manuscript."
    )
    topic = st.text_input(
        "Research Topic / Paper Title",
        placeholder="e.g. Sarcasm Detection in Indonesian Social Media using NLP"
    )
    target_journal = st.text_input(
        "Target Journal (optional)",
        placeholder="e.g. Information Processing & Management"
    )
    citation_style = st.selectbox("Citation Style", ["apa", "ieee", "acm"], index=0)
    output_format  = st.selectbox("Output Format", ["both", "docx", "latex"], index=0)

    st.divider()
    run_full = st.button("▶ Run Full Pipeline", type="primary", use_container_width=True)
    st.divider()
    st.caption("Run individual steps:")
    cols = st.columns(2)
    btn_research   = cols[0].button("/research",       use_container_width=True)
    btn_literature = cols[1].button("/literature",     use_container_width=True)
    btn_gap        = cols[0].button("/find-gap",       use_container_width=True)
    btn_citation   = cols[1].button("/check-citation", use_container_width=True)
    btn_intro      = cols[0].button("/write-intro",    use_container_width=True)
    btn_method     = cols[1].button("/write-method",   use_container_width=True)
    btn_results    = cols[0].button("/analyze-results",use_container_width=True)
    btn_figures    = cols[1].button("/create-figures", use_container_width=True)
    btn_tables     = cols[0].button("/create-tables",  use_container_width=True)
    btn_reviewer   = cols[1].button("/reviewer",       use_container_width=True)
    btn_plagiarism = cols[0].button("/plagiarism-risk",use_container_width=True)
    btn_scopus     = cols[1].button("/scopus-check",   use_container_width=True)
    btn_audit      = cols[0].button("/final-audit",    use_container_width=True)
    btn_format     = cols[1].button("/format",         use_container_width=True)


# ── Main area ─────────────────────────────────────────────────────────────────
st.title("Scopus Q1 Research Paper Generator")
st.caption("Transform a GitHub research repository into a submission-ready academic manuscript.")

# ── Input validation helper ───────────────────────────────────────────────────
def _validate_inputs() -> bool:
    if not github_url or not github_url.startswith("http"):
        st.error("Please enter a valid GitHub repository URL.")
        return False
    if not topic:
        st.error("Please enter a research topic.")
        return False
    return True


def _get_or_create_state() -> PaperState:
    if st.session_state.paper_state and st.session_state.paper_state.topic == topic:
        return st.session_state.paper_state
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
    st.session_state.paper_state = state
    return state


# ── Progress log ─────────────────────────────────────────────────────────────
log_container = st.container()
progress_placeholder = st.empty()
status_placeholder   = st.empty()


def _add_log(msg: str):
    st.session_state.log_lines.append(msg)


def _render_log():
    with log_container:
        if st.session_state.log_lines:
            st.markdown("### Activity Log")
            log_text = "\n".join(st.session_state.log_lines[-60:])
            st.code(log_text, language=None)


# ── Pipeline runner (threaded) ────────────────────────────────────────────────
def _run_pipeline_threaded(state: PaperState, log_q: Queue):
    def cb(msg):
        log_q.put(msg)

    try:
        orch = Orchestrator(progress_cb=cb)
        updated = orch.run_pipeline(
            github_url=state.github_url,
            topic=state.topic,
            target_journal=state.target_journal,
            citation_style=state.citation_style,
            output_format=state.output_format,
            resume=False,
        )
        st.session_state.paper_state = updated
        log_q.put("__DONE__")
    except Exception as e:
        log_q.put(f"❌ Pipeline error: {e}")
        log_q.put("__DONE__")


def _run_command_threaded(command: str, state: PaperState, log_q: Queue):
    def cb(msg):
        log_q.put(msg)

    try:
        orch = Orchestrator(progress_cb=cb)
        updated = orch.run_command(command, state)
        st.session_state.paper_state = updated
        log_q.put("__DONE__")
    except Exception as e:
        log_q.put(f"❌ Command error: {e}")
        log_q.put("__DONE__")


def _execute(fn, *args):
    """Run fn in a thread, stream log to UI."""
    if st.session_state.running:
        st.warning("A task is already running.")
        return

    if not _validate_inputs():
        return

    state = _get_or_create_state()
    st.session_state.running = True
    st.session_state.log_lines = []

    log_q: Queue = Queue()
    t = threading.Thread(target=fn, args=(*args, state, log_q), daemon=True)
    t.start()

    prog = st.progress(0)
    step = 0
    with st.spinner("Running…"):
        while t.is_alive() or not log_q.empty():
            try:
                msg = log_q.get(timeout=0.3)
                if msg == "__DONE__":
                    break
                _add_log(msg)
                step = min(step + 3, 95)
                prog.progress(step)
            except Empty:
                pass

    prog.progress(100)
    st.session_state.running = False
    st.session_state.pipeline_done = True
    st.rerun()


# ── Button handlers ───────────────────────────────────────────────────────────
if run_full:
    _execute(_run_pipeline_threaded)

slash_map = {
    btn_research:   "/research",
    btn_literature: "/literature",
    btn_gap:        "/find-gap",
    btn_citation:   "/check-citation",
    btn_intro:      "/write-introduction",
    btn_method:     "/write-method",
    btn_results:    "/analyze-results",
    btn_figures:    "/create-figures",
    btn_tables:     "/create-tables",
    btn_reviewer:   "/reviewer",
    btn_plagiarism: "/plagiarism-risk",
    btn_scopus:     "/scopus-check",
    btn_audit:      "/final-audit",
    btn_format:     "/format",
}
for btn, cmd in slash_map.items():
    if btn:
        _execute(_run_command_threaded, cmd)
        break


# ── Render log ────────────────────────────────────────────────────────────────
_render_log()


# ── Phase status dashboard ────────────────────────────────────────────────────
state: PaperState = st.session_state.paper_state
if state:
    st.divider()
    st.markdown("### Pipeline Status")

    phase_labels = {
        "repo_audit":           "📁 Repository Audit",
        "research_reconstruction":"🔬 Research Reconstruction",
        "literature":           "📚 Literature Search",
        "gap_analysis":         "🔍 Gap Analysis",
        "novelty":              "💡 Novelty Statement",
        "writing":              "✍️  Writing",
        "citation_validation":  "🔗 Citation Validation",
        "review":               "🧑‍🔬 Review",
        "formatting":           "📄 Formatting",
        "final_audit":          "✅ Final Audit",
    }
    icon_map = {"done": "✅", "running": "🔄", "failed": "❌", "pending": "⬜", "skipped": "⏩"}

    cols = st.columns(5)
    for i, (phase_key, label) in enumerate(phase_labels.items()):
        status = state.phase_status.get(phase_key, "pending")
        icon = icon_map.get(status, "⬜")
        cols[i % 5].markdown(f"{icon} {label}")


# ── Output files ──────────────────────────────────────────────────────────────
if state and st.session_state.pipeline_done:
    st.divider()
    st.markdown("### Output Files")

    output_files = [
        ("final/manuscript_final.docx", "📄 manuscript_final.docx",  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("final/manuscript_final.tex",  "📄 manuscript_final.tex",   "text/plain"),
        ("final/manuscript_final.pdf",  "📄 manuscript_final.pdf",   "application/pdf"),
        ("audit/citation_audit.md",     "📋 citation_audit.md",      "text/markdown"),
        ("audit/reviewer_report.md",    "📋 reviewer_report.md",     "text/markdown"),
        ("audit/plagiarism_risk.md",    "📋 plagiarism_risk.md",     "text/markdown"),
        ("final/final_audit.md",        "📋 final_audit.md",         "text/markdown"),
        ("final/journal_targets.md",    "📋 journal_targets.md",     "text/markdown"),
        ("manuscript/references.bib",   "📚 references.bib",         "text/plain"),
        ("literature/references_verified.csv", "📊 references_verified.csv", "text/csv"),
    ]

    available = [(rel, label, mime) for rel, label, mime in output_files
                 if os.path.exists(state.path(rel))]

    if available:
        cols = st.columns(3)
        for i, (rel, label, mime) in enumerate(available):
            path = state.path(rel)
            with open(path, "rb") as f:
                data = f.read()
            cols[i % 3].download_button(
                label=label,
                data=data,
                file_name=os.path.basename(rel),
                mime=mime,
                use_container_width=True,
            )
    else:
        st.info("No output files yet. Run the pipeline to generate files.")


# ── Manuscript preview ────────────────────────────────────────────────────────
if state and state.manuscript:
    st.divider()
    st.markdown("### Manuscript Preview")

    section_labels = {
        "abstract":               "Abstract",
        "introduction":           "Introduction",
        "literature_review":      "Literature Review",
        "theoretical_framework":  "Theoretical Framework",
        "methodology":            "Methodology",
        "results":                "Results",
        "discussion":             "Discussion",
        "theoretical_implications":"Theoretical Implications",
        "practical_implications": "Practical Implications",
        "limitations":            "Limitations",
        "conclusion":             "Conclusion",
    }

    available_sections = [k for k in section_labels if state.manuscript.get(k)]
    if available_sections:
        selected_sec = st.selectbox(
            "View section:",
            options=available_sections,
            format_func=lambda k: section_labels[k]
        )
        text = state.manuscript.get(selected_sec, "")
        word_count = len(text.split())
        evidence_count = text.count("[EVIDENCE_REQUIRED")
        st.caption(f"Words: {word_count} | [EVIDENCE_REQUIRED] markers: {evidence_count}")
        if evidence_count > 0:
            st.warning(f"⚠️ This section contains {evidence_count} [EVIDENCE_REQUIRED] marker(s) that need attention before submission.")
        st.markdown(text)


# ── Gap analysis preview ──────────────────────────────────────────────────────
if state and state.gap_analysis:
    with st.expander("🔍 Research Gap Analysis"):
        st.markdown(state.gap_analysis)

if state and state.novelty_statement:
    with st.expander("💡 Novelty Statement"):
        st.markdown(state.novelty_statement)

if state and state.citation_audit:
    with st.expander("🔗 Citation Audit"):
        st.markdown(state.citation_audit)


# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "⚠️ This system never fabricates data, citations, or results. "
    "Every claim is traceable to a verified source. "
    "[EVIDENCE_REQUIRED] markers indicate missing evidence that must be addressed before submission."
)
