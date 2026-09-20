# Scopus Q1 Paper Generator

Transform a GitHub research repository into a submission-ready academic manuscript, automatically.

## What it does

1. **Clones and audits** your GitHub repository — identifies datasets, code, figures, documentation
2. **Reconstructs the research** — problem, objectives, RQs, methods, variables
3. **Searches real literature** — CrossRef, OpenAlex, Semantic Scholar APIs
4. **Analyzes the research gap** — builds a gap matrix from actual papers
5. **Derives novelty conservatively** — no exaggeration
6. **Writes all IMRaD sections** — grounded in evidence
7. **Validates every citation** — DOI verified via CrossRef
8. **Runs a reviewer loop** — 15-dimension scoring with auto-revision
9. **Outputs DOCX + LaTeX/PDF** — ready for submission

**Core principle:** Never fabricate data, citations, or results.
Every claim is traceable. Missing evidence is marked `[EVIDENCE_REQUIRED]`.

---

## Quick Start (Local)

```bash
git clone https://github.com/indri007/scopus-jurnal
cd scopus-jurnal
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API key
streamlit run app.py
```

---

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Set **Main file path**: `app.py`
5. Add your secrets via **Settings → Secrets**:

```toml
LLM_PROVIDER   = "openai"
OPENAI_API_KEY = "sk-..."
LLM_MODEL      = "gpt-4o"
```

---

## Slash Commands

| Command | What it does |
|---|---|
| `/research` | Audit repo + reconstruct research |
| `/literature` | Search CrossRef, OpenAlex, Semantic Scholar |
| `/find-gap` | Build gap matrix + novelty statement |
| `/check-citation` | Validate all DOIs via CrossRef |
| `/write-introduction` | Write introduction section |
| `/write-method` | Write methodology section |
| `/analyze-results` | Write results section |
| `/create-figures` | Generate matplotlib figures |
| `/create-tables` | Generate markdown + LaTeX tables |
| `/reviewer` | Run 15-dimension reviewer loop |
| `/plagiarism-risk` | Analyze plagiarism risk per section |
| `/scopus-check` | Find top 5 matching Scopus journals |
| `/final-audit` | Pre-submission checklist |
| `/format` | Generate DOCX + LaTeX output |

---

## Output Structure

```
papers/<slug>/
├── research_audit/
│   ├── repository_inventory.md
│   ├── research_reconstruction.md
│   ├── research_gap.md
│   └── novelty_statement.md
├── literature/
│   ├── search_results.json
│   ├── selected_papers.json
│   └── references_verified.csv
├── manuscript/
│   ├── abstract.md
│   ├── introduction.md
│   ├── methodology.md
│   ├── results.md
│   ├── discussion.md
│   ├── conclusion.md
│   └── references.bib
├── audit/
│   ├── citation_audit.md
│   ├── reviewer_report.md
│   └── plagiarism_risk.md
└── final/
    ├── manuscript_final.docx
    ├── manuscript_final.tex
    ├── manuscript_final.pdf
    ├── journal_targets.md
    └── final_audit.md
```

---

## Configuration

All settings via `.env` or Streamlit secrets:

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | LLM backend |
| `OPENAI_API_KEY` | — | OpenAI key |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `SEMANTIC_SCHOLAR_API_KEY` | — | Optional, improves rate limits |
| `SCOPUS_API_KEY` | — | Optional, for journal search |
| `MAX_LITERATURE` | `20` | Papers per API source |
| `CITATION_STYLE` | `apa` | apa / ieee / acm |
| `OUTPUT_FORMAT` | `both` | docx / latex / both |

---

## Failure Policy

- Missing evidence → `[EVIDENCE_REQUIRED]`
- Unverifiable source → not cited
- Unreproducible result → not presented as verified
- Weak novelty → stated conservatively, not exaggerated
