"""
Phase 1+2 — Repository Auditor
Clones/reads a GitHub repo, builds an inventory, and reconstructs the research.
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from core.llm import chat
from core.state import PaperState
import config

logger = logging.getLogger(__name__)

# File extensions considered evidence-bearing
EVIDENCE_EXTS = {
    ".csv", ".xlsx", ".xls", ".json", ".jsonl",
    ".py", ".ipynb", ".r", ".rmd", ".sql",
    ".md", ".txt", ".pdf", ".docx",
    ".png", ".jpg", ".svg",
    ".pkl", ".pt", ".h5", ".model",
}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "env"}


# ── Public entry point ────────────────────────────────────────────────────────

def run(state: PaperState, progress_cb=None) -> PaperState:
    """Clone repo, build inventory, reconstruct research."""
    state.mark_running("repo_audit")
    _cb(progress_cb, "Cloning repository…")

    repo_path = _clone_repo(state.github_url, state.slug)
    if repo_path is None:
        state.mark_failed("repo_audit", "Failed to clone repository")
        return state

    _cb(progress_cb, "Scanning files…")
    file_tree = _scan_repo(repo_path)

    _cb(progress_cb, "Building inventory with LLM…")
    inventory_md = _build_inventory(file_tree, state.topic)
    state.inventory = inventory_md
    state.write_file("research_audit/repository_inventory.md", inventory_md)

    _cb(progress_cb, "Reconstructing research…")
    reconstruction_md = _reconstruct_research(file_tree, inventory_md, state.topic, repo_path)
    state.reconstruction = reconstruction_md
    state.write_file("research_audit/research_reconstruction.md", reconstruction_md)

    state.mark_done("repo_audit", state.path("research_audit/repository_inventory.md"))
    state.mark_done("research_reconstruction", state.path("research_audit/research_reconstruction.md"))
    state.save()

    _cb(progress_cb, "✅ Repository audit complete")
    return state


# ── Clone ─────────────────────────────────────────────────────────────────────

def _clone_repo(github_url: str, slug: str) -> Optional[str]:
    """Clone the repo into a temp dir inside the paper workspace."""
    dest = os.path.join(config.paper_dir(slug), "repo_clone")
    if os.path.exists(dest):
        logger.info("Repo already cloned at %s", dest)
        return dest

    os.makedirs(dest, exist_ok=True)
    env = os.environ.copy()
    if config.GITHUB_TOKEN:
        # Inject token into URL
        clean_url = re.sub(r"https://", f"https://{config.GITHUB_TOKEN}@", github_url)
    else:
        clean_url = github_url

    cmd = ["git", "clone", "--depth", "1", clean_url, dest]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        logger.error("git clone failed: %s", result.stderr)
        return None
    return dest


# ── File scan ─────────────────────────────────────────────────────────────────

def _scan_repo(repo_path: str) -> list[dict]:
    """Walk the repo and return a list of file metadata dicts."""
    files = []
    root = Path(repo_path)
    for path in sorted(root.rglob("*")):
        # Skip hidden dirs and noise
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        rel = str(path.relative_to(root))
        size = path.stat().st_size
        preview = _read_preview(path, ext)
        files.append({
            "path":     rel,
            "ext":      ext,
            "size_kb":  round(size / 1024, 1),
            "preview":  preview,
            "is_evidence": ext in EVIDENCE_EXTS,
        })
    return files


def _read_preview(path: Path, ext: str, max_chars: int = 800) -> str:
    """Read first max_chars of a text file as preview."""
    text_exts = {".py", ".ipynb", ".r", ".rmd", ".sql", ".md", ".txt", ".csv", ".json", ".jsonl"}
    if ext not in text_exts:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception:
        return ""


# ── Inventory ─────────────────────────────────────────────────────────────────

def _build_inventory(file_tree: list[dict], topic: str) -> str:
    # Build a compact representation for the LLM
    lines = []
    for f in file_tree:
        lines.append(
            f"- {f['path']} ({f['ext']}, {f['size_kb']}KB)"
            + (f"\n  preview: {f['preview'][:300]}" if f['preview'] else "")
        )
    tree_text = "\n".join(lines[:200])  # cap at 200 files

    prompt = f"""You are auditing a research GitHub repository for a paper on: "{topic}".

Repository file tree:
{tree_text}

Produce a markdown table inventory with these columns:
| Filename | File Type | Purpose | Research Relevance | Contains Empirical Evidence | Can Be Cited |

Rules:
- Be specific about purpose based on filename and preview.
- Research Relevance: High / Medium / Low / None
- Contains Empirical Evidence: Yes / No / Partial
- Can Be Cited: Yes / No

After the table, write a short paragraph (3–5 sentences) summarising the overall research content of this repository.

DO NOT fabricate files that are not listed above.
"""
    return chat(prompt, system="You are a rigorous academic repository auditor.")


# ── Research Reconstruction ────────────────────────────────────────────────────

def _reconstruct_research(file_tree: list[dict], inventory: str, topic: str, repo_path: str) -> str:
    # Gather key file contents for context
    context_files = _gather_key_files(file_tree, repo_path)

    prompt = f"""You are reconstructing the research design from a GitHub repository.

Topic: {topic}

Repository inventory summary:
{inventory[:2000]}

Key file contents:
{context_files}

Reconstruct the following and classify every statement with one of:
[DATA] [CODE] [DOCUMENTATION] [DERIVED_RESULT] [LITERATURE] [ASSUMPTION] [EVIDENCE_REQUIRED]

Sections to produce (use markdown headings):
## Research Problem
## Research Objectives
## Research Questions
## Theoretical Framework
## Dataset
## Sampling Method
## Variables
## Preprocessing
## Analytical Methods
## Validation Methods
## Limitations

Rules:
- NEVER convert an [ASSUMPTION] into a fact.
- If information is absent, write [EVIDENCE_REQUIRED: describe what is missing].
- Do NOT invent results.
"""
    return chat(prompt, system="You are a rigorous academic research reconstructor.")


def _gather_key_files(file_tree: list[dict], repo_path: str, max_total_chars: int = 6000) -> str:
    """Read README, main notebooks, and key scripts for LLM context."""
    priority_names = {"readme.md", "readme.txt", "main.py", "main.ipynb",
                      "analysis.py", "analysis.ipynb", "model.py", "train.py"}
    key_files = []

    # Priority files first
    for f in file_tree:
        if os.path.basename(f["path"]).lower() in priority_names and f["ext"] in {".md", ".txt", ".py", ".ipynb"}:
            key_files.append(f)

    # Then notebooks and scripts
    for f in file_tree:
        if f["ext"] in {".ipynb", ".py", ".r"} and f not in key_files:
            key_files.append(f)

    chunks = []
    total = 0
    for f in key_files:
        content = f["preview"] or ""
        if not content:
            try:
                with open(os.path.join(repo_path, f["path"]), "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read(1500)
            except Exception:
                continue
        chunk = f"\n\n--- {f['path']} ---\n{content[:1500]}"
        total += len(chunk)
        chunks.append(chunk)
        if total >= max_total_chars:
            break

    return "".join(chunks) or "(no readable source files found)"


def _cb(fn, msg: str):
    if fn:
        fn(msg)
    else:
        logger.info(msg)
