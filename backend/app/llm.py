"""LLM access layer - supports Claude (Anthropic) and Gemini (Google).

* `structured()` returns a validated Pydantic object.
* `text()` returns free text (summaries, reports).

Provider selection (LLM_PROVIDER): "auto" picks Claude when an Anthropic key is
set, otherwise Gemini when a Gemini key is set, otherwise rule-based mode.
Both functions return ``None`` when the LLM is unavailable or fails, and callers
fall back to deterministic rule-based logic, so the platform always works.
"""
import json
import logging
import os
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger("llm")
T = TypeVar("T", bound=BaseModel)

_clients: dict = {}
_last_error: str | None = None

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _anthropic_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"))


def _gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def provider() -> str | None:
    """'anthropic', 'gemini' or None (rule-based)."""
    if config.LLM_MODE == "rules":
        return None
    p = config.LLM_PROVIDER
    if p == "anthropic":
        return "anthropic" if (_anthropic_key() or config.LLM_MODE == "claude") else None
    if p == "gemini":
        return "gemini" if _gemini_key() else None
    if _anthropic_key():
        return "anthropic"
    if _gemini_key():
        return "gemini"
    return None


def enabled() -> bool:
    return provider() is not None


def model_name() -> str | None:
    p = provider()
    if p == "gemini":
        return _clients.get("gemini_last_model") or _gemini_models()[0]
    return {"anthropic": config.ANTHROPIC_MODEL}.get(p)


def status() -> dict:
    p = provider()
    return {"mode": "llm" if p else "rules", "provider": p, "model": model_name(), "last_error": _last_error}


def _record_error(e: Exception) -> None:
    global _last_error
    _last_error = f"{type(e).__name__}: {e}"[:500]
    log.warning("LLM call failed, falling back to rules: %s", _last_error)


def _anthropic():
    if "anthropic" not in _clients:
        import anthropic
        _clients["anthropic"] = anthropic.Anthropic()
    return _clients["anthropic"]


def _gemini():
    if "gemini" not in _clients:
        from google import genai
        from google.genai import types
        _clients["gemini"] = genai.Client(api_key=_gemini_key(),
                                          http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    return _clients["gemini"]


# ----------------------------------------------------------------------------- Claude
def _claude_structured(system: str, prompt: str, schema: type[T], max_tokens: int) -> T | None:
    resp = _anthropic().beta.messages.parse(
        model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}], output_format=schema,
        betas=[FALLBACK_BETA], fallbacks="default",  # server-side fallback if the request is declined
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused request")
    return resp.parsed_output


def _claude_text(system: str, prompt: str, max_tokens: int) -> str | None:
    resp = _anthropic().beta.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}], output_config={"effort": "low"},
        betas=[FALLBACK_BETA], fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused request")
    return "".join(b.text for b in resp.content if b.type == "text").strip() or None


# ----------------------------------------------------------------------------- Gemini
# GEMINI_MODEL may be a comma-separated fallback chain. A model that answers "busy" (429/503)
# or "not found" (404) is skipped for a cool-down period and the next model is tried.
_busy_until: dict[str, float] = {}
COOLDOWN_S = 300
MAX_ATTEMPTS = 2          # models tried per call
TIMEOUT_MS = 25_000       # per model attempt
logging.getLogger("google_genai.models").setLevel(logging.WARNING)


def _gemini_models() -> list[str]:
    chain = [m.strip() for m in config.GEMINI_MODEL.split(",") if m.strip()]
    now = time.time()
    ready = [m for m in chain if _busy_until.get(m, 0) <= now]
    return ready or chain  # if all are cooling down, try them all anyway


def _gemini_call(**kwargs):
    import httpx
    from google.genai import errors
    last: Exception | None = None
    for model in _gemini_models()[:MAX_ATTEMPTS]:
        try:
            resp = _gemini().models.generate_content(model=model, **kwargs)
            _clients["gemini_last_model"] = model
            return resp
        except (errors.APIError, httpx.TimeoutException) as e:
            code = getattr(e, "code", "timeout")
            if isinstance(e, httpx.TimeoutException) or code in (404, 429, 500, 502, 503, 504):
                _busy_until[model] = time.time() + COOLDOWN_S
                log.info("Gemini model %s unavailable (%s); trying next", model, code)
                last = e
                continue
            raise
    raise last or RuntimeError("no Gemini model available")


def _gemini_structured(system: str, prompt: str, schema: type[T], max_tokens: int) -> T | None:
    from google.genai import types
    try:
        resp = _gemini_call(
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               response_schema=schema, max_output_tokens=max_tokens),
        )
        if isinstance(resp.parsed, schema):
            return resp.parsed
        return schema.model_validate_json(resp.text)
    except (ValidationError, ValueError) as e:
        # Some schemas are not accepted as response_schema - retry with the schema in the prompt.
        log.info("Gemini schema mode failed (%s); retrying with JSON-in-prompt", type(e).__name__)
    resp = _gemini_call(
        contents=f"{prompt}\n\nRespond with ONLY a JSON object matching this JSON schema:\n"
                 f"{json.dumps(schema.model_json_schema())}",
        config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                           max_output_tokens=max_tokens),
    )
    return schema.model_validate_json(resp.text)


def _gemini_text(system: str, prompt: str, max_tokens: int) -> str | None:
    from google.genai import types
    resp = _gemini_call(
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens),
    )
    return (resp.text or "").strip() or None


# ----------------------------------------------------------------------------- public API
def structured(system: str, prompt: str, schema: type[T], max_tokens: int = 4000) -> T | None:
    p = provider()
    if p is None:
        return None
    try:
        fn = _claude_structured if p == "anthropic" else _gemini_structured
        return fn(system, prompt, schema, max_tokens)
    except Exception as e:  # noqa: BLE001 - any failure -> rule-based fallback
        _record_error(e)
        return None


def text(system: str, prompt: str, max_tokens: int = 4000) -> str | None:
    p = provider()
    if p is None:
        return None
    try:
        fn = _claude_text if p == "anthropic" else _gemini_text
        return fn(system, prompt, max_tokens)
    except Exception as e:  # noqa: BLE001
        _record_error(e)
        return None
