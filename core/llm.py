"""LLM client — OpenAI-compatible interface with retry logic."""

import logging
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=config.API_KEY, base_url=config.API_BASE_URL)


def _get_anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=config.API_KEY)


def chat(
    prompt: str,
    system: str = "You are a rigorous academic research assistant.",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    retries: int = None,
) -> str:
    """
    Send a chat completion request. Returns the response text.
    Raises RuntimeError after exhausting retries.
    """
    model       = model       or config.MODEL_NAME
    temperature = temperature or config.TEMPERATURE
    max_tokens  = max_tokens  or config.MAX_TOKENS
    retries     = retries     if retries is not None else config.MAX_RETRIES

    last_error = None
    for attempt in range(1, retries + 2):
        try:
            if config.LLM_PROVIDER == "anthropic":
                return _anthropic_chat(prompt, system, model, temperature, max_tokens)
            else:
                return _openai_chat(prompt, system, model, temperature, max_tokens)
        except Exception as exc:
            last_error = exc
            wait = 2 ** attempt
            logger.warning("LLM attempt %d/%d failed: %s — retrying in %ds", attempt, retries + 1, exc, wait)
            if attempt <= retries:
                time.sleep(wait)

    raise RuntimeError(f"LLM failed after {retries + 1} attempts: {last_error}") from last_error


def _openai_chat(prompt, system, model, temperature, max_tokens) -> str:
    client = _get_openai_client()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
    )
    return response.choices[0].message.content.strip()


def _anthropic_chat(prompt, system, model, temperature, max_tokens) -> str:
    client = _get_anthropic_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()
