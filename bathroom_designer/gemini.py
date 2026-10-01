"""Talk to Google Gemini: API key storage and picking a model that still exists.

Google retires model names regularly. If a request fails because the model is
gone, we use the replacement Google names in the error, or else the newest
suitable model on the account, and remember it for the rest of the run.
"""

import os
import re
import time
from pathlib import Path

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY_FILE = Path(__file__).resolve().parent / ".gemini_key"
_chosen = {}          # "text" / "image" -> model that worked


def get_key(explicit=""):
    """Key from the form/project file, else the environment, else the saved key."""
    key = (explicit or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    return key


def save_key(key):
    key = (key or "").strip()
    if key and key != get_key():
        KEY_FILE.write_text(key)
        try:
            KEY_FILE.chmod(0o600)
        except OSError:
            pass


def _version(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def pick_model(key, want, exclude=()):
    """Newest model on this account for text+vision ('text') or image output ('image')."""
    resp = requests.get(f"{BASE}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=30)
    resp.raise_for_status()
    names = [m["name"].split("/")[-1] for m in resp.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    if want == "image":
        cands = [n for n in names if "image" in n]
    else:
        skip = ("image", "tts", "live", "embedding", "audio", "lite", "thinking")
        cands = [n for n in names if "flash" in n and not any(s in n for s in skip)]
        cands = cands or [n for n in names if "pro" in n and not any(s in n for s in skip)]
    cands = [n for n in cands if n not in exclude]
    if not cands:
        raise RuntimeError(f"No Gemini {want} model is available on this API key.")
    return max(cands, key=lambda n: (_version(n), "preview" not in n and "exp" not in n))


def generate(body, key, model, want, timeout=300):
    """POST generateContent, switching to a current model if this one was retired."""
    model = _chosen.get(want, model)
    tried = set()
    busy_waits = [5, 15, 30]          # seconds; Google's "high demand" errors are usually brief
    while True:
        tried.add(model)
        resp = requests.post(f"{BASE}/models/{model}:generateContent", json=body, timeout=timeout,
                             headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        if resp.status_code == 200:
            _chosen[want] = model
            return resp.json()
        text = resp.text
        if resp.status_code in (429, 500, 503):
            if busy_waits:
                wait = busy_waits.pop(0)
                print(f"  Gemini is busy ({resp.status_code}) - retrying in {wait}s")
                time.sleep(wait)
                tried.discard(model)
                continue
            try:                      # still busy: try another current model once
                alt = pick_model(key, want, exclude=tried)
            except Exception:
                alt = None
            if alt:
                print(f"  {model} is still busy - trying {alt}")
                model, busy_waits = alt, [10]
                continue
            raise RuntimeError("Google's Gemini servers are busy right now. Wait a few minutes and try again.")
        gone = resp.status_code == 404 or "no longer available" in text or "not found" in text.lower()
        if not gone:
            if resp.status_code in (400, 403) and "API key" in text:
                raise RuntimeError("Gemini rejected the API key. Paste it again in Settings (copy it fresh from aistudio.google.com/apikey).")
            raise RuntimeError(f"Gemini returned {resp.status_code}: {text[:300]}")
        suggested = re.findall(r"models/([\w.\-]+)", text)
        if want == "image":                       # a text-only suggestion can't draw images
            suggested = [s for s in suggested if "image" in s]
        nxt = next((s for s in suggested if s not in tried and s != model), None)
        if nxt is None:
            nxt = pick_model(key, want)
        if nxt in tried:
            raise RuntimeError(f"Gemini model {model} isn't available and no replacement was found: {text[:200]}")
        print(f"  Gemini model {model} is retired - using {nxt}")
        model = nxt
