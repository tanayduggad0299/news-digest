"""One interface to whichever language model we're using.

Everything downstream (impact scoring, claim extraction, synthesis) calls
generate_structured() and never knows or cares which provider answered. That
keeps provider choice a one-line config edit instead of a rewrite, which
matters while we are still deciding whether a free model is good enough.

Providers:
  gemini     Google Gemini free tier — no cost, rate limited, data may be used
             by Google for product improvement.
  anthropic  Claude — paid per token, strongest instruction-following.

Both support *structured output*: we hand the model a JSON Schema and the API
constrains generation so the reply must match it. We never parse prose.
"""

import json
import os
import time
import urllib.error
import urllib.request

from . import config

_GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
               "models/{model}:generateContent?key={key}")


class LLMError(RuntimeError):
    pass


class LLMRefusal(RuntimeError):
    """The model declined to answer. Not a bug — handle and move on."""


class QuotaExhausted(RuntimeError):
    """This model's DAILY allowance is gone. Retrying cannot succeed — every
    attempt spends another request from an empty budget. Switch models."""


def _classify_http(exc):
    """Decide whether an HTTP error is worth retrying.

    The distinction that matters: a 429 can mean 'slow down' (per-minute rate
    limit, retry works) or 'you are out for the day' (per-day quota, retry is
    actively harmful). Google distinguishes them in the error body's quotaId,
    so we read it rather than guessing from the status code.
    """
    try:
        body = json.loads(exc.read() or b"{}")
    except Exception:
        body = {}
    err = body.get("error", {})
    quota_ids = [v.get("quotaId", "")
                 for det in err.get("details", [])
                 for v in det.get("violations", [])]
    if exc.code == 429 and any("PerDay" in q for q in quota_ids):
        return "exhausted", err.get("message", "")[:160]
    if exc.code in (429, 500, 502, 503, 529):
        return "retry", err.get("message", "")[:160]
    return "fatal", err.get("message", "")[:160]


def _load_env():
    from dotenv import load_dotenv
    load_dotenv(config.PROJECT_ROOT / ".env")


# ------------------------------------------------------------------ gemini --

def _gemini(system, user, schema, model, max_tokens):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY not set — add it to news-digest/.env")

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "maxOutputTokens": max_tokens,
        },
    }
    req = urllib.request.Request(
        _GEMINI_URL.format(model=model, key=key),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    raw = json.loads(urllib.request.urlopen(req, timeout=120).read())

    candidates = raw.get("candidates") or []
    if not candidates:
        # Gemini reports content-policy blocks here rather than as an HTTP error.
        raise LLMRefusal(str(raw.get("promptFeedback", "no candidates returned")))

    cand = candidates[0]
    if cand.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        raise LLMRefusal(cand.get("finishReason"))

    text = "".join(p.get("text", "")
                   for p in cand.get("content", {}).get("parts", []))
    if not text.strip():
        raise LLMError(f"empty response (finishReason={cand.get('finishReason')})")

    usage = raw.get("usageMetadata", {})
    return json.loads(text), {
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
    }


# --------------------------------------------------------------- anthropic --

def _anthropic(system, user, schema, model, max_tokens):
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY not set — add it to news-digest/.env")

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    # Claude can decline with HTTP 200 and stop_reason 'refusal' — always check
    # before touching .content.
    if resp.stop_reason == "refusal":
        raise LLMRefusal(str(getattr(resp, "stop_details", "refusal")))

    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text), {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }


# ------------------------------------------------------------------ public --

_PROVIDERS = {"gemini": _gemini, "anthropic": _anthropic}

# Models whose daily quota ran out during this process.
_exhausted = set()
_call_count = [0]


def generate_structured(system, user, schema, *, provider=None, model=None,
                        max_tokens=2000, max_retries=None):
    """Call the active model and return (parsed_dict, usage_dict).

    Two failure modes are handled differently, which is the whole point:

      transient (503, per-minute 429, network)  -> retry the same model
      daily quota exhausted                     -> abandon that model for the
                                                   rest of the run and fail over
                                                   to the next in the chain

    Conflating them is what burned a full day's free quota in one run: every
    "retry" of a per-day 429 spends another request that cannot possibly work.
    """
    _load_env()
    if _call_count[0] >= config.MAX_CALLS_PER_RUN:
        raise LLMError(f"run call budget exhausted ({config.MAX_CALLS_PER_RUN}); "
                       f"refusing further requests so the remaining daily quota "
                       f"survives for the next run")
    provider = provider or config.LLM_PROVIDER
    max_retries = config.LLM_MAX_RETRIES if max_retries is None else max_retries
    fn = _PROVIDERS[provider]

    if model:
        chain = [model]
    else:
        chain = [m for m in config.LLM_MODEL_CHAIN.get(provider,
                 [config.LLM_MODELS[provider]]) if m not in _exhausted]
        if not chain:
            raise QuotaExhausted(
                f"every {provider} model in the chain is out of daily quota: "
                f"{sorted(_exhausted)}")

    last = None
    for candidate in chain:
        for attempt in range(max_retries + 1):
            try:
                _call_count[0] += 1
                return fn(system, user, schema, candidate, max_tokens)
            except LLMRefusal:
                raise
            except urllib.error.HTTPError as exc:
                kind, msg = _classify_http(exc)
                last = f"HTTP {exc.code}: {msg}"
                if kind == "exhausted":
                    _exhausted.add(candidate)
                    break                      # no retries; try the next model
                if kind == "fatal":
                    raise LLMError(last) from exc
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"

            if attempt < max_retries:
                time.sleep(config.LLM_BACKOFF_SECONDS * (2 ** attempt))

    raise LLMError(f"all models failed ({', '.join(chain)}); last error: {last}")


def exhausted_models():
    return sorted(_exhausted)


def call_count():
    return _call_count[0]


def reset_counters():
    _exhausted.clear()
    _call_count[0] = 0
