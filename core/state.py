"""Paper project state — tracks progress, files, and phase outputs."""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import config


PHASES = [
    "repo_audit",
    "research_reconstruction",
    "data_validation",
    "reproducibility",
    "literature",
    "gap_analysis",
    "novelty",
    "writing",
    "citation_validation",
    "review",
    "formatting",
    "final_audit",
]

STATUS_PENDING    = "pending"
STATUS_RUNNING    = "running"
STATUS_DONE       = "done"
STATUS_FAILED     = "failed"
STATUS_SKIPPED    = "skipped"


@dataclass
class PaperState:
    slug:           str
    github_url:     str
    topic:          str
    target_journal: str        = ""
    citation_style: str        = "apa"
    output_format:  str        = "both"
    language:       str        = "english"

    phase_status:   Dict[str, str]  = field(default_factory=dict)
    phase_outputs:  Dict[str, str]  = field(default_factory=dict)   # phase → primary output path
    errors:         List[str]       = field(default_factory=list)
    warnings:       List[str]       = field(default_factory=list)
    created_at:     float           = field(default_factory=time.time)
    updated_at:     float           = field(default_factory=time.time)

    # Phase-specific payloads (in-memory, also serialised)
    inventory:          Optional[str]  = None   # repo_auditor markdown
    reconstruction:     Optional[str]  = None
    validated_results:  Optional[str]  = None
    reproducibility_log:Optional[str]  = None
    selected_papers:    Optional[list] = None   # list of dicts
    gap_analysis:       Optional[str]  = None
    novelty_statement:  Optional[str]  = None
    manuscript:         Dict[str, str] = field(default_factory=dict)  # section → text
    citation_audit:     Optional[str]  = None
    reviewer_reports:   List[str]      = field(default_factory=list)
    review_round:       int            = 0

    def __post_init__(self):
        for p in PHASES:
            if p not in self.phase_status:
                self.phase_status[p] = STATUS_PENDING

    # ── Convenience ──────────────────────────────────────────────────────────
    def mark_running(self, phase: str):
        self.phase_status[phase] = STATUS_RUNNING
        self.updated_at = time.time()

    def mark_done(self, phase: str, output_path: str = ""):
        self.phase_status[phase] = STATUS_DONE
        if output_path:
            self.phase_outputs[phase] = output_path
        self.updated_at = time.time()

    def mark_failed(self, phase: str, error: str):
        self.phase_status[phase] = STATUS_FAILED
        self.errors.append(f"[{phase}] {error}")
        self.updated_at = time.time()

    def is_done(self, phase: str) -> bool:
        return self.phase_status.get(phase) == STATUS_DONE

    @property
    def workspace(self) -> str:
        return config.paper_dir(self.slug)

    def path(self, *parts) -> str:
        """Resolve a path inside this paper's workspace."""
        return os.path.join(self.workspace, *parts)

    # ── Persistence ──────────────────────────────────────────────────────────
    def save(self):
        os.makedirs(self.workspace, exist_ok=True)
        path = self.path("state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, slug: str) -> "PaperState":
        path = os.path.join(config.paper_dir(slug), "state.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No state found for slug: {slug}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def load_or_create(cls, slug: str, **kwargs) -> "PaperState":
        try:
            return cls.load(slug)
        except FileNotFoundError:
            return cls(slug=slug, **kwargs)

    # ── Directory helpers ────────────────────────────────────────────────────
    def ensure_dirs(self):
        for d in [
            "research_audit",
            "analysis",
            "literature",
            "manuscript",
            "audit",
            "figures",
            "tables",
            "final",
            "build",
        ]:
            os.makedirs(self.path(d), exist_ok=True)

    def write_file(self, rel_path: str, content: str) -> str:
        """Write content to a file inside the workspace. Returns absolute path."""
        abs_path = self.path(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return abs_path

    def read_file(self, rel_path: str) -> str:
        abs_path = self.path(rel_path)
        if not os.path.exists(abs_path):
            return ""
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()
