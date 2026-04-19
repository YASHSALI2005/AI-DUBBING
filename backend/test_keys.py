"""
API Key Health Check
====================
Tests Gemini TTS, Gemini Text, and Sarvam API keys from .env

Usage:
    python test_keys.py
"""

import os, sys, json, base64, io, urllib.request, urllib.parse, urllib.error
from dotenv import load_dotenv

# Force UTF-8 output on Windows so special chars print cleanly
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

GEMINI_KEY  = os.getenv("GEMINI_API_KEY", "")
SARVAM_KEY  = os.getenv("SARVAM_API_KEY", "")

GEMINI_TTS_MODEL  = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TEXT_MODEL = os.getenv("GEMINI_MODEL",     "gemini-2.5-flash")

SEP = "─" * 55

def _post(url: str, payload: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _gemini_url(model: str) -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={urllib.parse.quote(GEMINI_KEY)}"
    )

# ── 1. Gemini Text ────────────────────────────────────────────────────────────
def check_gemini_text():
    print(f"\n[1] Gemini Text  ({GEMINI_TEXT_MODEL})")
    if not GEMINI_KEY:
        print("    ✗  GEMINI_API_KEY not set in .env"); return

    payload = {"contents": [{"parts": [{"text": "Say 'OK' in one word."}]}],
               "generationConfig": {"temperature": 0}}
    try:
        data  = _post(_gemini_url(GEMINI_TEXT_MODEL), payload)
        reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"    ✓  Working  →  response: \"{reply}\"")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        _show_gemini_error(e.code, body)
    except Exception as e:
        print(f"    ✗  Error: {e}")

# ── 2. Gemini TTS ─────────────────────────────────────────────────────────────
def check_gemini_tts():
    print(f"\n[2] Gemini TTS   ({GEMINI_TTS_MODEL})")
    if not GEMINI_KEY:
        print("    ✗  GEMINI_API_KEY not set in .env"); return

    payload = {
        "contents": [{"parts": [{"text": "Say: Hello."}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "temperature": 1.0,
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
        },
    }
    try:
        data  = _post(_gemini_url(GEMINI_TTS_MODEL), payload, timeout=60)
        # Try to find inline audio bytes
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        found = any(p.get("inlineData") or p.get("inline_data") for p in parts)
        if found:
            print("    ✓  Working  →  audio data received successfully")
        else:
            print(f"    ?  Got response but no audio part: {json.dumps(data)[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        _show_gemini_error(e.code, body)
    except Exception as e:
        print(f"    ✗  Error: {e}")

# ── 3. Sarvam TTS ─────────────────────────────────────────────────────────────
def check_sarvam():
    print("\n[3] Sarvam TTS   (bulbul:v3)")
    if not SARVAM_KEY:
        print("    ✗  SARVAM_API_KEY not set in .env"); return

    url     = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs":           ["नमस्ते"],
        "target_language_code": "hi-IN",
        "speaker":          "priya",
        "model":            "bulbul:v3",
        "speech_sample_rate": 22050,
    }
    headers = {
        "Content-Type":         "application/json",
        "api-subscription-key": SARVAM_KEY,
    }
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp  = json.loads(r.read().decode("utf-8"))
        audios = resp.get("audios") or []
        if audios and audios[0]:
            print("    ✓  Working  →  audio data received successfully")
        else:
            print(f"    ?  Got response but no audio: {json.dumps(resp)[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        code = e.code
        if code == 401:
            print(f"    ✗  Invalid API key (401 Unauthorized)")
        elif code == 429:
            print(f"    ✗  Rate limit / quota exceeded (429)")
        else:
            print(f"    ✗  HTTP {code}: {body[:300]}")
    except Exception as e:
        print(f"    ✗  Error: {e}")

# ── helpers ───────────────────────────────────────────────────────────────────
def _show_gemini_error(code: int, body: str):
    try:
        err = json.loads(body).get("error", {})
        msg = err.get("message", body[:200])
    except Exception:
        msg = body[:200]

    if code == 429:
        if "resource_exhausted" in msg.lower() or "quota" in msg.lower():
            # Extract retry delay if present
            import re
            m = re.search(r"Please retry in (.+?)\.", msg)
            eta = f"  (retry in {m.group(1)})" if m else ""
            print(f"    ✗  QUOTA EXHAUSTED (429) — daily limit reached{eta}")
        else:
            print(f"    ✗  Rate limited (429): {msg[:200]}")
    elif code == 403:
        print(f"    ✗  Invalid / unauthorized key (403): {msg[:200]}")
    elif code == 400:
        print(f"    ✗  Bad request (400) — model may not support TTS or wrong config: {msg[:200]}")
    else:
        print(f"    ✗  HTTP {code}: {msg[:200]}")

# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(SEP)
    print("  API Key Health Check")
    print(SEP)
    print(f"  GEMINI_API_KEY : {'*' * 6}{GEMINI_KEY[-6:] if len(GEMINI_KEY) > 6 else '(not set)'}")
    print(f"  SARVAM_API_KEY : {'*' * 6}{SARVAM_KEY[-6:] if len(SARVAM_KEY) > 6 else '(not set)'}")

    check_gemini_text()
    check_gemini_tts()
    check_sarvam()

    print(f"\n{SEP}")
