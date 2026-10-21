"""Minimal chat client for Groq (OpenAI-compatible) and local Ollama.

Model names are written as "provider:model", e.g.
    groq:openai/gpt-oss-20b
    ollama:gemma4:e4b
A bare name defaults to groq. Stdlib only, no SDK needed.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"


def _load_dotenv():
    # repo/.env first, then one level up (handy when several eval repos share keys)
    root = Path(__file__).resolve().parent.parent
    for env in (root / ".env", root.parent / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    if v.strip():
                        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()


def _groq_keys():
    # GROQ_API_KEYS=key1,key2,... lets us rotate keys when one hits the free-tier limit
    raw = os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


_key_idx = 0
# Server-side timing of the most recent successful Groq call (seconds). Measured by Groq,
# so it doesn't include time we spent waiting on rate limits.
LAST_USAGE = {}


def _post(url, payload, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "driftbench/0.1", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def chat(model, messages, temperature=0.0, max_tokens=2048, timeout=300):
    provider, _, name = model.partition(":") if ":" in model else ("groq", "", model)
    if provider not in ("groq", "ollama"):
        provider, name = "groq", model

    if provider == "ollama":
        data = _post(OLLAMA_URL, {"model": name, "messages": messages, "stream": False,
                                  "options": {"temperature": temperature, "seed": 7}}, {}, timeout)
        text = data["message"]["content"]
    else:
        global _key_idx
        keys = _groq_keys()
        if not keys:
            raise SystemExit("set GROQ_API_KEY or GROQ_API_KEYS (env var or .env file)")
        payload = {"model": name, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens, "seed": 7}
        if name.startswith("openai/gpt-oss"):
            # gpt-oss thinks before answering; low effort keeps us inside the 8k tokens/min free tier
            payload["reasoning_effort"] = "low"
        deadline = time.time() + 900  # rate limits are shared across runs, so be patient
        for attempt in range(10_000):
            key = keys[_key_idx % len(keys)]
            try:
                data = _post(GROQ_URL, payload, {"Authorization": f"Bearer {key}"}, timeout)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and time.time() < deadline:
                    _key_idx += 1  # spread load across keys (only helps if they are on different accounts)
                    time.sleep(max(float(e.headers.get("retry-after") or 0), min(30, 2 ** min(attempt, 5))) + 1)
                    continue
                raise
        text = data["choices"][0]["message"]["content"] or ""
        LAST_USAGE.clear()
        LAST_USAGE.update(data.get("usage") or {})

    # reasoning models sometimes leak their scratchpad, drop it before grading
    return THINK_RE.sub("", text).strip()
