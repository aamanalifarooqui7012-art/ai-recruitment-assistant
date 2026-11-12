"""The one place Phase 3B talks to Groq (retries, rate-limit waits, JSON repair). Logic unchanged from the standalone app."""
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from project_config import section

from .errors import EvalError

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

CFG = section("phase3b")                                  # config/config.yaml -> phase3b
MODEL = os.getenv("GROQ_MODEL") or CFG["model"]
LLM_URL = CFG["base_url"].rstrip("/") + "/chat/completions"
KEY_ENV = CFG["api_key_env"]                              # second Groq account: Phase 3 only (Phase 1 + 2 use GROQ_API_KEY)


def parse_json(s):
    s = re.sub(r"```json|```", "", s or "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no json")
    return json.loads(s[a:b + 1])


def _wait_from(msg, headers, attempt):
    ra = headers.get("retry-after")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    m = re.search(r"(?:try again|retry) in (?:(\d+)m)?([\d.]+)s", msg, re.I)
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2)) + 1
    return 5.0 * (attempt + 1)


def groq_json(messages, validate, log=print):
    key = os.getenv(KEY_ENV, "").strip()
    if not key or "your_key" in key or "paste_your" in key:
        raise EvalError(f"{KEY_ENV} is not set in .env. Add the key and restart the app.")
    tries, last, fmt = int(CFG.get("max_tries", 4)), "", True
    soft = (ValueError, KeyError, IndexError, TypeError)
    for a in range(tries):
        payload = {"model": MODEL, "messages": messages, "temperature": CFG.get("temperature", 0.2),
                   "reasoning_effort": CFG.get("reasoning_effort", "low"),
                   "max_completion_tokens": int(CFG.get("max_output_tokens", 8000))}
        if fmt:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(LLM_URL, json=payload, timeout=int(CFG.get("timeout_seconds", 120)),
                              headers={"Authorization": f"Bearer {key}"})
        except requests.Timeout:
            last = "The request did not finish within 2 minutes (timeout)."
            log(f"{last} Retry {a + 1}/{tries}")
            time.sleep(2 * (a + 1))
            continue
        except requests.RequestException:
            last = "Network error. Check your internet, VPN or firewall."
            log(f"{last} Retry {a + 1}/{tries}")
            time.sleep(2 * (a + 1))
            continue

        if r.ok:
            try:
                content = r.json()["choices"][0]["message"].get("content") or ""
                return validate(parse_json(content))
            except soft as e:
                last = f"The model's output was not valid ({e})."
                log(f"{last} Retry {a + 1}/{tries}")
                continue

        try:
            body = r.json()
        except ValueError:
            body = {}
        if isinstance(body, list):
            body = body[0] if body and isinstance(body[0], dict) else {}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        msg = err.get("message") or f"HTTP {r.status_code}"
        code = r.status_code
        if code == 401:
            raise EvalError("The Groq API key is wrong or expired (401). Check the .env file.")
        if code == 403:
            raise EvalError("Access denied (403). Check the key and its region/permissions.")
        if code == 404:
            raise EvalError(f"Model {MODEL} is not available for this key. {msg}")
        if code == 413:
            raise EvalError("The request is larger than the model allows (413). Use a shorter transcript.")
        if code == 400:
            if re.search(r"api key", msg, re.I):
                raise EvalError("The Groq API key is wrong or expired. Check the .env file.")
            if re.search(r"failed to generate json|json_validate_failed", msg + str(err.get("code", "")), re.I):
                fg = err.get("failed_generation")
                if fg:
                    try:
                        return validate(parse_json(fg))
                    except soft:
                        pass
                if fmt:
                    fmt = False
                    last = f"Bad request: {msg[:120]}"
                    log(f"JSON mode was not accepted. Retrying with it off ({a + 1}/{tries})")
                    time.sleep(1)
                    continue
            raise EvalError("Bad request: " + msg)
        if code == 429 or code >= 500:
            w = _wait_from(msg, r.headers, a)
            last = f"Rate limit (429): {msg}" if code == 429 else f"Groq server error ({code})"
            if w > 90:
                raise EvalError(f"The Groq limit was hit. Try again in about {int(w)} seconds. {msg[:140]}")
            log(f"Waiting {int(w) + 1}s... {last[:90]}")
            time.sleep(w)
            continue
        raise EvalError(msg)
    raise EvalError("Failed even after several tries: " + last)
