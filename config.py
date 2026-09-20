"""Scopus Q1 Paper Generator — Configuration."""

import os
import logging
import yaml

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ── LLM ──────────────────────────────────────────────────────────────────────
_PROVIDERS = {
    "openai":    {"base_url": "https://api.openai.com/v1",            "model": "gpt-4o",          "env_key": "OPENAI_API_KEY"},
    "deepseek":  {"base_url": "https://api.deepseek.com",             "model": "deepseek-chat",   "env_key": "DEEPSEEK_API_KEY"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1",         "model": "claude-3-5-sonnet-20241022", "env_key": "ANTHROPIC_API_KEY"},
}

LLM_PROVIDER  = os.environ.get("LLM_PROVIDER", "openai")
_prov         = _PROVIDERS.get(LLM_PROVIDER, _PROVIDERS["openai"])
API_KEY       = os.environ.get(_prov["env_key"], "")
API_BASE_URL  = os.environ.get("LLM_BASE_URL", _prov["base_url"])
MODEL_NAME    = os.environ.get("LLM_MODEL",    _prov["model"])
TEMPERATURE   = 0.2
MAX_TOKENS    = 8192

# ── Literature APIs ───────────────────────────────────────────────────────────
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
SCOPUS_API_KEY           = os.environ.get("SCOPUS_API_KEY", "")
MAX_LITERATURE           = int(os.environ.get("MAX_LITERATURE", "20"))
CROSSREF_ENABLED         = True
CROSSREF_TIMEOUT         = 10.0

# ── GitHub ────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# ── Output ────────────────────────────────────────────────────────────────────
PROJECT_ROOT   = os.path.dirname(os.path.abspath(__file__))
PAPERS_DIR     = os.path.join(PROJECT_ROOT, "papers")
TEMPLATES_DIR  = os.path.join(PROJECT_ROOT, "templates")
OUTPUT_FORMAT  = os.environ.get("OUTPUT_FORMAT", "both")   # docx | latex | both
CITATION_STYLE = os.environ.get("CITATION_STYLE", "apa")   # apa | ieee | acm

# ── Agent ─────────────────────────────────────────────────────────────────────
MAX_WORKERS            = 4
MAX_RETRIES            = 3
AGENT_TIMEOUT          = 180
REVIEW_PASS_THRESHOLD  = 7.0   # out of 10
MAX_REVIEW_ROUNDS      = 3


def paper_dir(slug: str) -> str:
    """Return the workspace root for a paper slug."""
    return os.path.join(PAPERS_DIR, slug)


def load_config(path: str):
    """Load YAML overrides into this module's globals."""
    if not os.path.exists(path):
        logger.warning("Config file not found: %s", path)
        return
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _map = {
        "model":            "MODEL_NAME",
        "temperature":      "TEMPERATURE",
        "max_tokens":       "MAX_TOKENS",
        "citation_style":   "CITATION_STYLE",
        "output_format":    "OUTPUT_FORMAT",
        "max_literature":   "MAX_LITERATURE",
        "max_workers":      "MAX_WORKERS",
        "max_retries":      "MAX_RETRIES",
        "review_threshold": "REVIEW_PASS_THRESHOLD",
        "max_review_rounds":"MAX_REVIEW_ROUNDS",
    }
    for yaml_key, cfg_var in _map.items():
        if yaml_key in data:
            globals()[cfg_var] = data[yaml_key]
    logger.info("Config loaded from %s", path)
