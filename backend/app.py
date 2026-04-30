import os
import sys
import json
import uuid
import time
import re
import math
import asyncio
import mimetypes
import base64
import io
import subprocess
import shutil
import traceback
import urllib.request
import urllib.error
import urllib.parse
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydub import AudioSegment
from dotenv import load_dotenv

# The N-speaker single-TTS pipeline lives in scratch/tts_experiment.py
_SCRATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch")
if _SCRATCH_DIR not in sys.path:
    sys.path.insert(0, _SCRATCH_DIR)
import tts_experiment as gemini_seg  # type: ignore[import-not-found]

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Keys ──────────────────────────────────────────────────────────────────
ELEVEN_KEY      = os.getenv("ELEVEN_API_KEY")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")

# ── Config from env ───────────────────────────────────────────────────────────
WORKSPACE_DIR   = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR        = os.path.join(WORKSPACE_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

TEMP_RETENTION_HOURS            = int(os.getenv("TEMP_RETENTION_HOURS", "24"))
SYNTHESIS_MAX_WORKERS           = int(os.getenv("SYNTHESIS_MAX_WORKERS", "2"))
SYNTHESIS_RETRY_ATTEMPTS        = int(os.getenv("SYNTHESIS_RETRY_ATTEMPTS", "4"))
SYNTHESIS_RETRY_BASE_SECONDS    = float(os.getenv("SYNTHESIS_RETRY_BASE_SECONDS", "1.0"))
TRANSLATE_MAX_WORKERS           = int(os.getenv("TRANSLATE_MAX_WORKERS", "4"))
TRANSLATE_RETRY_ATTEMPTS        = int(os.getenv("TRANSLATE_RETRY_ATTEMPTS", "3"))
TRANSLATE_RETRY_BASE_SECONDS    = float(os.getenv("TRANSLATE_RETRY_BASE_SECONDS", "1.0"))

# ElevenLabs STT timing heuristics
STT_SECONDS_PER_MINUTE_AUDIO    = float(os.getenv("STT_SECONDS_PER_MINUTE_AUDIO", "20"))
STT_MIN_ESTIMATE_SECONDS        = float(os.getenv("STT_MIN_ESTIMATE_SECONDS", "10"))

TRANSLATE_SECONDS_PER_BLOCK     = float(os.getenv("TRANSLATE_SECONDS_PER_BLOCK", "0.9"))
GEMINI_SECONDS_PER_BLOCK        = float(os.getenv("GEMINI_SECONDS_PER_BLOCK", "4.0"))

ELEVEN_BASE_URL                     = os.getenv("ELEVEN_BASE_URL", "https://api.elevenlabs.io")
ELEVEN_DUBBING_POLL_INTERVAL_SECONDS = float(os.getenv("ELEVEN_DUBBING_POLL_INTERVAL_SECONDS", "4"))
ELEVEN_DUBBING_TIMEOUT_SECONDS      = int(os.getenv("ELEVEN_DUBBING_TIMEOUT_SECONDS", "600"))

GEMINI_MODEL                = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
GEMINI_MAX_WORKERS          = int(os.getenv("GEMINI_MAX_WORKERS", "1"))
GEMINI_RETRY_ATTEMPTS       = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "5"))
GEMINI_RETRY_BASE_SECONDS   = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "2.0"))
GEMINI_RETRY_MAX_SECONDS    = float(os.getenv("GEMINI_RETRY_MAX_SECONDS", "45.0"))
GEMINI_MIN_LENGTH_RATIO     = float(os.getenv("GEMINI_MIN_LENGTH_RATIO", "0.75"))

# ElevenLabs STT model — use the scribe_v1 diarization model
ELEVEN_STT_MODEL = os.getenv("ELEVEN_STT_MODEL", "scribe_v1")

# -----------------
# Pydantic Models
# -----------------
class TranslateRequest(BaseModel):
    transcript_blocks: List[Dict[str, Any]]
    target_lang: str
    source_lang: str = "en"
    speaker_genders: Dict[str, str] = Field(default_factory=dict)


class SynthesisVoiceMap(BaseModel):
    speaker_id: str
    voice_id: str


class SynthesisRequest(BaseModel):
    session_id: Optional[str] = None
    transcript_blocks: List[Dict[str, Any]]
    voice_map: List[SynthesisVoiceMap]
    speaker_genders: Dict[str, str] = Field(default_factory=dict)
    target_duration_ms: float = 0
    target_lang: str = "hi-IN"
    auto_detect_speakers: bool = True
    disable_voice_cloning: bool = False
    synthesis_mode: str = "batched_per_speaker_gemini"


class EnhanceTranscriptRequest(BaseModel):
    session_id: str
    transcript_blocks: List[Dict[str, Any]]
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None
    speaker_genders: Dict[str, str] = {}


class GeminiSessionPreviewRequest(BaseModel):
    session_id: str
    target_lang: Optional[str] = None


class DirectDubFinalizeRequest(BaseModel):
    session_id: str
    dubbing_id: str
    target_lang: str


# -----------------
# Helper Functions
# -----------------

def cleanup_expired_sessions() -> int:
    deleted = 0
    cutoff = time.time() - (TEMP_RETENTION_HOURS * 3600)
    for name in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, name)
        if not os.path.isdir(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                deleted += 1
        except Exception as exc:
            print(f"Cleanup skipped for {path}: {exc}")
    return deleted


def extract_audio(video_path: str, audio_path: str):
    command = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        audio_path,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def normalize_lang_code(lang: str) -> str:
    """Map locale-like codes (hi-IN) to ISO code (hi) for ElevenLabs."""
    if not lang:
        return "auto"
    return lang.split("-")[0].lower()


def eleven_headers() -> Dict[str, str]:
    if not ELEVEN_KEY:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY is not configured.")
    return {"xi-api-key": ELEVEN_KEY}


def get_audio_duration_seconds_ffprobe(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        return float((proc.stdout or "").strip())
    except Exception:
        return 0.0


def get_audio_duration_seconds(path: str) -> float:
    dur = get_audio_duration_seconds_ffprobe(path)
    if dur > 0:
        return dur
    try:
        return len(AudioSegment.from_file(path)) / 1000.0
    except Exception:
        return 0.0


def estimate_stt_wall_seconds(audio_duration_sec: float) -> float:
    if audio_duration_sec <= 0:
        return STT_MIN_ESTIMATE_SECONDS
    return max(STT_MIN_ESTIMATE_SECONDS, (audio_duration_sec / 60.0) * STT_SECONDS_PER_MINUTE_AUDIO)


def estimate_translate_wall_seconds(api_calls: int) -> float:
    if api_calls <= 0:
        return 2.0
    waves = math.ceil(api_calls / max(1, TRANSLATE_MAX_WORKERS))
    return max(3.0, waves * TRANSLATE_SECONDS_PER_BLOCK)


def estimate_gemini_enhance_wall_seconds(non_empty_blocks: int) -> float:
    if non_empty_blocks <= 0:
        return 0.0
    waves = math.ceil(non_empty_blocks / max(1, GEMINI_MAX_WORKERS))
    return max(5.0, waves * GEMINI_SECONDS_PER_BLOCK)


# ── ElevenLabs STT ────────────────────────────────────────────────────────────

def eleven_stt_transcribe(audio_path: str, language_code: Optional[str] = None) -> Dict[str, Any]:
    """Call ElevenLabs Speech-to-Text with diarization via /v1/speech-to-text.

    Returns the raw ElevenLabs response dict which includes:
      words[] with speaker_id, text, start, end
      language_code, language_probability
    """
    if not ELEVEN_KEY:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY is not configured.")

    boundary = f"----ElevenBoundary{uuid.uuid4().hex}"
    filename  = os.path.basename(audio_path)
    mime_type = mimetypes.guess_type(filename)[0] or "audio/mpeg"

    with open(audio_path, "rb") as fh:
        file_data = fh.read()

    parts: List[bytes] = []

    # file field
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
    parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode())
    parts.append(file_data)
    parts.append(b"\r\n")

    # model_id field
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="model_id"\r\n\r\n')
    parts.append(ELEVEN_STT_MODEL.encode())
    parts.append(b"\r\n")

    # diarize field
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="diarize"\r\n\r\n')
    parts.append(b"true")
    parts.append(b"\r\n")

    # timestamps field (word-level)
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="timestamps_granularity"\r\n\r\n')
    parts.append(b"word")
    parts.append(b"\r\n")

    # language_code (optional)
    if language_code:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="language_code"\r\n\r\n')
        parts.append(language_code.encode())
        parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        url=f"{ELEVEN_BASE_URL}/v1/speech-to-text",
        method="POST",
        data=body,
        headers={
            **eleven_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"ElevenLabs STT failed [{exc.code}]: {detail}")
        raise HTTPException(status_code=exc.code, detail=f"ElevenLabs STT failed: {detail}")


def eleven_stt_to_blocks(stt_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert ElevenLabs STT word-level response into transcript blocks.

    Groups consecutive words from the same speaker into a single block,
    returning the same schema the frontend already understands:
      [{id, speakers, transcript, timestamps}]
    """
    words = stt_response.get("words") or []
    if not words:
        # Fallback: return single block from full text
        full_text = stt_response.get("text", "")
        return [{
            "id": "block-0",
            "speakers": ["speaker_0"],
            "transcript": full_text,
            "timestamps": [0.0, 0.0],
        }]

    blocks: List[Dict[str, Any]] = []
    current_speaker: Optional[str] = None
    current_words: List[str] = []
    current_start: float = 0.0
    current_end: float = 0.0
    block_idx = 0

    for word_obj in words:
        word_type     = word_obj.get("type", "word")
        text          = word_obj.get("text", "")
        speaker_id    = word_obj.get("speaker_id") or "speaker_0"
        word_start    = float(word_obj.get("start") or 0.0)
        word_end      = float(word_obj.get("end") or word_start)

        # Skip spacing tokens — they carry no speech info
        if word_type == "spacing":
            if current_words:
                current_words.append(text)  # preserve spaces inside block
            continue

        if speaker_id != current_speaker:
            # Flush previous block
            if current_words and current_speaker is not None:
                merged_text = "".join(current_words).strip()
                if merged_text:
                    blocks.append({
                        "id": f"block-{block_idx}",
                        "speakers": [current_speaker],
                        "transcript": merged_text,
                        "timestamps": [round(current_start, 3), round(current_end, 3)],
                    })
                    block_idx += 1
            current_speaker = speaker_id
            current_words   = [text]
            current_start   = word_start
            current_end     = word_end
        else:
            current_words.append(text)
            current_end = word_end

    # Flush last block
    if current_words and current_speaker is not None:
        merged_text = "".join(current_words).strip()
        if merged_text:
            blocks.append({
                "id": f"block-{block_idx}",
                "speakers": [current_speaker],
                "transcript": merged_text,
                "timestamps": [round(current_start, 3), round(current_end, 3)],
            })

    return blocks


# ── ElevenLabs Translation ────────────────────────────────────────────────────

def eleven_translate_text(
    text: str,
    target_lang: str,
    source_lang: str = "auto",
) -> str:
    """Translate a single text segment using ElevenLabs /v1/translate/text.

    Falls back to returning the original text on failure so the pipeline
    keeps running even when individual blocks fail.
    """
    if not ELEVEN_KEY:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY is not configured.")

    # Normalize to ISO 639-1 code (hi-IN -> hi)
    tgt = normalize_lang_code(target_lang)
    src = "auto" if (not source_lang or source_lang.lower() == "auto") else normalize_lang_code(source_lang)

    payload = json.dumps({
        "text": text,
        "target_language": tgt,
        **({"source_language": src} if src != "auto" else {}),
    }).encode("utf-8")

    req = urllib.request.Request(
        url=f"{ELEVEN_BASE_URL}/v1/translate/text",
        method="POST",
        data=payload,
        headers={
            **eleven_headers(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("translated_text") or data.get("text") or text
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"ElevenLabs translate failed [{exc.code}]: {detail}")
        raise HTTPException(status_code=exc.code, detail=f"ElevenLabs translate failed: {detail}")


def translate_text_with_retry(
    text: str,
    source_lang: str,
    target_lang: str,
    speaker_gender: Optional[str] = None,  # kept for API compat, unused
) -> str:
    """Retry ElevenLabs translation on transient errors."""
    for attempt in range(TRANSLATE_RETRY_ATTEMPTS):
        try:
            return eleven_translate_text(text, target_lang, source_lang)
        except HTTPException as exc:
            is_retryable = exc.status_code in (429, 500, 503)
            last_attempt = attempt == TRANSLATE_RETRY_ATTEMPTS - 1
            if not is_retryable or last_attempt:
                raise
            sleep_seconds = TRANSLATE_RETRY_BASE_SECONDS * (2 ** attempt)
            print(f"ElevenLabs translate transient error. Retry {attempt + 1}/{TRANSLATE_RETRY_ATTEMPTS} in {sleep_seconds:.1f}s.")
            time.sleep(sleep_seconds)
        except Exception as exc:
            last_attempt = attempt == TRANSLATE_RETRY_ATTEMPTS - 1
            if last_attempt:
                raise
            sleep_seconds = TRANSLATE_RETRY_BASE_SECONDS * (2 ** attempt)
            print(f"Translate error (retry {attempt + 1}): {exc}")
            time.sleep(sleep_seconds)
    # Unreachable but satisfies type checkers
    return text


# ── ElevenLabs Dubbing (end-to-end) ──────────────────────────────────────────

def eleven_post_dubbing(
    file_path: str,
    target_lang: str,
    source_lang: str = "auto",
    num_speakers: int = 0,
    disable_voice_cloning: bool = False,
) -> Dict[str, Any]:
    boundary = f"----CursorBoundary{uuid.uuid4().hex}"
    filename  = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(file_path, "rb") as fh:
        file_data = fh.read()

    parts: List[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
    parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode())
    parts.append(file_data)
    parts.append(b"\r\n")
    for key, value in [
        ("target_lang", target_lang),
        ("source_lang", source_lang),
        ("num_speakers", num_speakers),
        ("disable_voice_cloning", str(disable_voice_cloning).lower()),
    ]:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        parts.append(str(value).encode())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        url=f"{ELEVEN_BASE_URL}/v1/dubbing",
        method="POST",
        data=body,
        headers={
            **eleven_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"ElevenLabs create dubbing failed [{exc.code}]: {detail}")
        raise HTTPException(status_code=exc.code, detail=f"ElevenLabs dubbing create failed: {detail}")


def is_unsupported_target_language_error(detail: str) -> bool:
    detail_l = (detail or "").lower()
    return "unsupported_target_language" in detail_l or "target language" in detail_l


def eleven_get_dubbing(dubbing_id: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        url=f"{ELEVEN_BASE_URL}/v1/dubbing/{dubbing_id}",
        method="GET",
        headers=eleven_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=exc.code, detail=f"ElevenLabs dubbing status failed: {detail}")


def eleven_download_dubbed_file(dubbing_id: str, language_code: str) -> bytes:
    req = urllib.request.Request(
        url=f"{ELEVEN_BASE_URL}/v1/dubbing/{dubbing_id}/audio/{language_code}",
        method="GET",
        headers=eleven_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=exc.code, detail=f"ElevenLabs dubbed file download failed: {detail}")


# ── Gemini enhancement helpers ────────────────────────────────────────────────

def extract_audio_chunk_base64(audio_path: str, start_s: float, end_s: float, max_seconds: float = 8.0) -> str:
    full_audio = AudioSegment.from_file(audio_path)
    start_ms = max(0, int(start_s * 1000))
    end_ms   = max(start_ms, int(end_s * 1000))
    if end_ms - start_ms > int(max_seconds * 1000):
        end_ms = start_ms + int(max_seconds * 1000)
    chunk = full_audio[start_ms:end_ms]
    if len(chunk) == 0:
        chunk = AudioSegment.silent(duration=1000)
    buffer = io.BytesIO()
    chunk.export(buffer, format="wav")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def call_gemini_for_transcript_enhancement(
    text: str,
    audio_chunk_b64: str,
    speaker_label: str,
    speaker_gender: str,
    source_lang: str,
    target_lang: str,
) -> str:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    prompt = f"""
You are a strict dubbing transcript post-editor for TTS.

Task:
Refine the current translated line so it sounds natural when spoken, but stay very close to the original meaning and structure. Add appropriate inline audio tags in square brackets (e.g., [laughs], [whispers], [excitedly]) to guide the TTS delivery, pacing, and emotional vibe.

Hard constraints:
- Output language must be exactly: {target_lang}
- Audio tags MUST strictly be in English enclosed in square brackets (e.g., [sighs], [very fast], [sarcastically]), even if {target_lang} is not English.
- Source-language context for disambiguation: {source_lang}
- Keep named entities unchanged (person/place/organization names).
- Do NOT add new facts or implications not present in the line, but DO use audio tags to express the underlying emotional context.
- Keep clause order mostly unchanged.
- Use simple, clear spoken wording that a TTS voice pronounces reliably.
- Avoid fancy idioms/slang unless explicitly present.
- Match tone from the audio chunk (speaker={speaker_label}, gender_hint={speaker_gender}) by placing relevant audio tags at the start of the line or inline right before specific phrases (e.g., "[amazed] Wow, [whispers] I didn't see that coming.").
- Return exactly one cleaned line including the inserted audio tags, no labels, no notes, no quotation marks.

Current translated line:
{text}
""".strip()

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "audio/wav", "data": audio_chunk_b64}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.4},
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={urllib.parse.quote(GEMINI_API_KEY)}"
    )
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=exc.code, detail=f"Gemini enhancement failed: {detail}")

    candidates = data.get("candidates") or []
    if not candidates:
        return text
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    merged_text = " ".join((p.get("text") or "").strip() for p in parts if p.get("text")).strip()
    if not merged_text:
        return text

    src = text.strip()
    out = merged_text.strip()
    if len(src) >= 80:
        length_ratio = len(out) / max(1, len(src))
        src_clauses = len(re.findall(r"[,.!?;:]", src))
        out_clauses = len(re.findall(r"[,.!?;:]", out))
        if length_ratio < GEMINI_MIN_LENGTH_RATIO or (src_clauses >= 2 and out_clauses < max(1, src_clauses - 1)):
            return text

    return merged_text


def infer_retry_delay_seconds(error_text: str) -> Optional[float]:
    if not error_text:
        return None
    patterns = [
        r"Please retry in\s*([0-9]+(?:\.[0-9]+)?)s",
        r"\"retryDelay\"\s*:\s*\"([0-9]+(?:\.[0-9]+)?)s\"",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_text, flags=re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                return None
    return None


def extract_emotion_tags_and_clean_text(text: str) -> Tuple[List[str], str]:
    if not text:
        return [], ""
    tags    = [m.strip() for m in re.findall(r"\[([^\[\]]+)\]", text) if m.strip()]
    cleaned = re.sub(r"\[[^\[\]]+\]", " ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return tags, cleaned


def call_gemini_with_retry(
    text: str,
    audio_chunk_b64: str,
    speaker_label: str,
    speaker_gender: str,
    source_lang: str,
    target_lang: str,
) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
        try:
            return call_gemini_for_transcript_enhancement(
                text=text,
                audio_chunk_b64=audio_chunk_b64,
                speaker_label=speaker_label,
                speaker_gender=speaker_gender,
                source_lang=source_lang,
                target_lang=target_lang,
            )
        except HTTPException as exc:
            last_exc = exc
            if exc.status_code not in (429, 503):
                raise
            if attempt >= GEMINI_RETRY_ATTEMPTS:
                raise
            retry_after = infer_retry_delay_seconds(str(exc.detail or ""))
            if retry_after is None:
                retry_after = min(GEMINI_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), GEMINI_RETRY_MAX_SECONDS)
            sleep_seconds = min(retry_after + 0.25, GEMINI_RETRY_MAX_SECONDS)
            print(f"[Gemini retry] attempt={attempt}/{GEMINI_RETRY_ATTEMPTS} status={exc.status_code} wait={sleep_seconds:.2f}s")
            time.sleep(sleep_seconds)
        except Exception as exc:
            last_exc = exc
            if attempt >= GEMINI_RETRY_ATTEMPTS:
                raise
            time.sleep(min(GEMINI_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), GEMINI_RETRY_MAX_SECONDS))
    if last_exc:
        raise last_exc
    return text


# ── Upload + STT sync helper ──────────────────────────────────────────────────

def _upload_extract_and_stt_sync(
    video_path: str,
    audio_path: str,
    language_code: Optional[str],
) -> Dict[str, Any]:
    """Blocking: ffmpeg extract + ElevenLabs diarized STT. Run in thread pool."""
    try:
        t_extract0 = time.perf_counter()
        extract_audio(video_path, audio_path)
        audio_extract_seconds = time.perf_counter() - t_extract0
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Audio extraction failed: {str(e)}") from e

    audio_duration_seconds = get_audio_duration_seconds(audio_path)
    estimated_stt_seconds  = estimate_stt_wall_seconds(audio_duration_seconds)

    try:
        t_stt0 = time.perf_counter()
        stt_response = eleven_stt_transcribe(audio_path, language_code)
        stt_job_seconds = time.perf_counter() - t_stt0

        blocks = eleven_stt_to_blocks(stt_response)

        return {
            "blocks": blocks,
            "language": stt_response.get("language_code") or "auto",
            "language_probability": stt_response.get("language_probability"),
            "audio_duration_seconds": round(audio_duration_seconds, 2),
            "audio_extract_seconds": round(audio_extract_seconds, 2),
            "stt_job_seconds": round(stt_job_seconds, 2),
            "estimated_stt_seconds": round(estimated_stt_seconds, 1),
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"STT Pipeline failed: {str(e)}") from e


# ── N-speaker batched TTS pipeline ───────────────────────────────────────────

def _synthesize_batched_per_speaker_sync(
    session_dir: str,
    session_id: str,
    target_lang: str,
    target_duration_ms: float,
    analysis_model: str,
    manual_blocks: Optional[List[Dict[str, Any]]] = None,
    manual_voice_map: Optional[Dict[str, str]] = None,
    manual_speaker_genders: Optional[Dict[str, str]] = None,
    manual_mode: bool = False,
) -> Dict[str, Any]:
    """Batched-per-speaker Gemini TTS pipeline.

    Instead of one TTS request per segment, we group all segments for
    each speaker into a single batched request (using [pause] markers).
    For N speakers, only N requests are fired — all concurrently via threads.
    The returned audio blobs are split back into per-segment chunks using
    silence detection, then overlaid onto a single timeline.

    Falls back to per-segment synthesis when batching/splitting fails.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    from pathlib import Path
    from google import genai as _genai

    audio_path = os.path.join(session_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise HTTPException(
            status_code=404,
            detail="Session audio.mp3 not found. Upload via /api/upload first.",
        )

    client = _genai.Client(api_key=GEMINI_API_KEY)

    def _build_manual_analysis(
        blocks: List[Dict[str, Any]],
        voice_map: Dict[str, str],
        speaker_genders: Dict[str, str],
        manual_lang: str,
    ) -> Dict[str, Any]:
        segments: List[Dict[str, Any]] = []
        speakers: Dict[str, Dict[str, Any]] = {}
        for idx, block in enumerate(blocks):
            text = (block.get("transcript") or "").strip()
            if not text:
                continue
            raw_speakers = block.get("speakers") or []
            spk   = str(raw_speakers[0]) if raw_speakers else f"S{idx+1}"
            ts    = block.get("timestamps") or []
            start_s = float(ts[0]) if isinstance(ts, list) and len(ts) > 0 else float(idx * 2)
            end_s   = float(ts[1]) if isinstance(ts, list) and len(ts) > 1 else (start_s + 2.0)
            if end_s <= start_s:
                end_s = start_s + 0.5
            voice  = (voice_map.get(spk) or "").strip()
            gender = (speaker_genders.get(spk) or "unknown").lower()
            if spk not in speakers:
                speakers[spk] = {
                    "name": spk,
                    "gender": gender if gender in ("male", "female") else "unknown",
                    "character_archetype": "Manual speaker",
                    "voice_reasoning": "Manual speaker settings from frontend",
                    "recommended_voice": voice,
                    "style_direction": "Use manually edited transcript and timeline.",
                }
            elif voice and not speakers[spk].get("recommended_voice"):
                speakers[spk]["recommended_voice"] = voice
            segments.append({
                "speaker": spk,
                "start": start_s,
                "end": end_s,
                "text": text,
                "tagged_text": text,
            })
        segments.sort(key=lambda s: float(s.get("start", 0.0)))
        for spk, cfg in speakers.items():
            if not cfg.get("recommended_voice"):
                cfg["recommended_voice"] = "Charon" if cfg.get("gender") == "male" else "Aoede"
        return {
            "transcript": " ".join(s.get("text", "") for s in segments).strip(),
            "num_speakers": len(speakers) or 1,
            "speakers": list(speakers.values()),
            "segments": segments,
            "tagged_transcript": "\n".join(f"{s['speaker']}: {s['tagged_text']}" for s in segments),
            "language": (manual_lang or "unknown"),
            "pace": "moderate",
        }

    # Step 1: get analysis
    if manual_mode and manual_blocks:
        analysis = _build_manual_analysis(
            manual_blocks,
            manual_voice_map or {},
            manual_speaker_genders or {},
            target_lang,
        )
    else:
        analysis = gemini_seg.analyse_audio(client, Path(audio_path), analysis_model)

    num_speakers = analysis.get("num_speakers", 1)
    segments     = analysis.get("segments", [])
    if not segments:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini analysis returned no 'segments' (num_speakers={num_speakers}).",
        )

    # Step 2: translate per-segment if target lang differs
    is_dubbing = bool(target_lang and target_lang != analysis.get("language", "en-US"))
    if is_dubbing:
        gemini_seg.translate_segments_in_place(client, analysis, target_lang, analysis_model)

    # Persist analysis
    analysis_to_save = dict(analysis)
    analysis_to_save["speaker_plan"]            = gemini_seg.build_speaker_plan(analysis)
    analysis_to_save["multi_speaker_mode_used"] = "batched_per_speaker"
    analysis_to_save["manual_mode"]             = bool(manual_mode and manual_blocks)
    if is_dubbing:
        analysis_to_save["dubbed_language"] = target_lang
    with open(os.path.join(session_dir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(analysis_to_save, f, indent=2, ensure_ascii=False)

    # Step 3+4: batched TTS → timeline stitch
    # synthesize_segments_to_timeline already implements per-speaker batching
    # via build_batched_speaker_tts_input + split_batched_speaker_audio.
    audio_out = Path(session_dir) / "final.wav"

    if target_duration_ms and target_duration_ms > 0:
        target_dur_s: Optional[float] = float(target_duration_ms) / 1000.0
    else:
        input_video  = os.path.join(session_dir, "input.mp4")
        source_media = input_video if os.path.exists(input_video) else audio_path
        target_dur_s = get_audio_duration_seconds(source_media) or None

    gemini_seg.synthesize_segments_to_timeline(
        client=client,
        analysis=analysis,
        audio_out=audio_out,
        output_dir=Path(session_dir),
        stem="render",
        lang_tag="",
        target_dur=target_dur_s,
        is_dubbing=is_dubbing,
        apply_tighten=is_dubbing,
        voice_override=None,
        no_speed_match=False,
        max_speed_match=1.2,
        no_exact_duration=False,
    )

    # Step 5: mux back onto original video if present
    final_video_url = None
    input_video = os.path.join(session_dir, "input.mp4")
    if os.path.exists(input_video):
        final_mp4 = os.path.join(session_dir, "final.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", input_video, "-i", str(audio_out),
            "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            final_mp4,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            final_video_url = f"/api/video/{session_id}"
        else:
            print(f"[BatchedDub] FFmpeg muxing error: {result.stderr[:500]}")

    return {
        "audio_url": f"/api/audio/{session_id}",
        "video_url": final_video_url,
        "provider": "gemini_batched_per_speaker",
        "num_speakers": num_speakers,
        "segment_count": len(segments),
        "speakers": [
            {
                "name": s.get("name"),
                "gender": s.get("gender"),
                "voice": gemini_seg.resolve_voice_for_speaker(s),
                "archetype": s.get("character_archetype"),
            }
            for s in analysis.get("speakers", [])
        ],
        "language_used": target_lang if is_dubbing else analysis.get("language"),
    }


# ── ElevenLabs direct-dub language list ──────────────────────────────────────

ELEVEN_DIRECT_DUB_LANGUAGES = [
    {"code": "hi",  "name": "Hindi",       "flag": "🇮🇳"},
    {"code": "ta",  "name": "Tamil",       "flag": "🇮🇳"},
    {"code": "te",  "name": "Telugu",      "flag": "🇮🇳"},
]


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/api/config")
async def get_config():
    return JSONResponse(content={
        "supported_target_languages": [lang["code"] for lang in ELEVEN_DIRECT_DUB_LANGUAGES],
        "timing": {
            "translate_max_workers": TRANSLATE_MAX_WORKERS,
            "translate_seconds_per_block": TRANSLATE_SECONDS_PER_BLOCK,
            "stt_seconds_per_minute_audio": STT_SECONDS_PER_MINUTE_AUDIO,
            "stt_min_estimate_seconds": STT_MIN_ESTIMATE_SECONDS,
            "gemini_max_workers": GEMINI_MAX_WORKERS,
            "gemini_seconds_per_block": GEMINI_SECONDS_PER_BLOCK,
        },
        "stt_provider": "elevenlabs",
        "translate_provider": "elevenlabs",
        "tts_provider": "gemini_batched_per_speaker",
    })


# ── Stage 1: Upload + ElevenLabs STT ─────────────────────────────────────────

@app.post("/api/upload")
async def process_upload(
    file: UploadFile = File(...),
    language_code: Optional[str] = Form(None),
):
    """Upload video/audio → extract audio → ElevenLabs diarized STT.

    Accepts: .mp4, .mov, .avi, .mkv, .webm (video) or .mp3, .wav, .m4a (audio).
    Returns diarized transcript blocks ready for Stage 2 review.
    """
    cleanup_expired_sessions()

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".ogg"}
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {ext}")

    session_id  = str(uuid.uuid4())
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    is_video   = ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    media_path = os.path.join(session_dir, f"input{ext}")
    audio_path = os.path.join(session_dir, "audio.mp3")

    # Stream to disk
    try:
        with open(media_path, "wb") as out:
            while True:
                chunk = await file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {str(e)}") from e

    # Also symlink as input.mp4 for the synthesize pipeline if it's video
    if is_video and media_path != os.path.join(session_dir, "input.mp4"):
        try:
            os.symlink(media_path, os.path.join(session_dir, "input.mp4"))
        except Exception:
            pass

    try:
        payload = await asyncio.to_thread(
            _upload_extract_and_stt_sync,
            media_path if is_video else media_path,  # ffmpeg handles audio direct too
            audio_path,
            language_code,
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Upload pipeline failed: {str(e)}") from e

    return JSONResponse(content={
        "session_id": session_id,
        "message": "Upload & ElevenLabs STT successful",
        **payload,
    })


@app.post("/api/upload-fast")
async def process_upload_fast(file: UploadFile = File(...)):
    """Upload + extract audio only — skip STT. For Gemini auto-analysis flow."""
    cleanup_expired_sessions()

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()

    session_id  = str(uuid.uuid4())
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    video_path = os.path.join(session_dir, "input.mp4")
    audio_path = os.path.join(session_dir, "audio.mp3")

    try:
        with open(video_path, "wb") as out:
            while True:
                chunk = await file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {str(e)}") from e

    def _extract_sync() -> float:
        t0 = time.perf_counter()
        extract_audio(video_path, audio_path)
        return time.perf_counter() - t0

    try:
        audio_extract_seconds = await asyncio.to_thread(_extract_sync)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Audio extraction failed: {str(e)}") from e

    audio_duration_seconds = get_audio_duration_seconds(audio_path)

    return JSONResponse(content={
        "session_id": session_id,
        "message": "Upload successful (STT skipped — Gemini will transcribe at synth time)",
        "audio_duration_seconds": round(audio_duration_seconds, 2),
        "audio_extract_seconds": round(audio_extract_seconds, 2),
    })


# ── Stage 3: ElevenLabs Translation ──────────────────────────────────────────

@app.post("/api/translate")
async def translate_text(req: TranslateRequest):
    """Translate transcript blocks via ElevenLabs /v1/translate/text.

    Each block is translated independently (parallelized). Returns the same
    block structure with 'transcript' replaced by the translated text.
    """
    translated_blocks = [None] * len(req.transcript_blocks)
    failed_blocks: List[Dict[str, Any]] = []

    # Count non-empty translatable blocks for ETA
    translate_api_calls = sum(
        1 for b in req.transcript_blocks
        if (b.get("transcript") or "").strip() and req.target_lang != req.source_lang
    )
    estimated_translate_seconds = estimate_translate_wall_seconds(translate_api_calls)
    t_translate0 = time.perf_counter()

    def process_block(index: int, block: Dict[str, Any]):
        speakers   = block.get("speakers", [])
        timestamps = block.get("timestamps", [])
        original   = block.get("transcript", "")

        try:
            if original.strip() and req.target_lang != req.source_lang:
                trans_text = translate_text_with_retry(
                    original,
                    req.source_lang,
                    req.target_lang,
                )
            else:
                trans_text = original
            error_text = None
        except Exception as e:
            print(f"Translation error for block {index}: {original[:50]}")
            traceback.print_exc()
            trans_text = original
            error_text = str(e)

        return index, {
            "transcript": trans_text,
            "speakers":   speakers,
            "timestamps": timestamps,
        }, error_text

    with ThreadPoolExecutor(max_workers=TRANSLATE_MAX_WORKERS) as executor:
        futures = [executor.submit(process_block, i, b) for i, b in enumerate(req.transcript_blocks)]
        for future in as_completed(futures):
            idx, res_block, error_text = future.result()
            translated_blocks[idx] = res_block
            if error_text:
                failed_blocks.append({"index": idx, "error": error_text})

    translation_processing_seconds = time.perf_counter() - t_translate0

    return JSONResponse(content={
        "blocks": translated_blocks,
        "failed_block_count": len(failed_blocks),
        "failed_blocks": failed_blocks,
        "translate_api_calls": translate_api_calls,
        "estimated_translate_seconds": round(estimated_translate_seconds, 1),
        "translation_processing_seconds": round(translation_processing_seconds, 2),
        "provider": "elevenlabs",
    })


# ── Stage 3: Gemini audio-tag enhancement ────────────────────────────────────

@app.post("/api/enhance-translation")
async def enhance_translation(req: EnhanceTranscriptRequest):
    """Send translated segments to Gemini 2.5 Pro to insert audio tags."""
    session_dir = os.path.join(TEMP_DIR, req.session_id)
    audio_path  = os.path.join(session_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Original session audio not found for enhancement.")

    resolved_source_lang = req.source_lang or "unknown"
    resolved_target_lang = req.target_lang or "unknown"
    enhanced_blocks      = [None] * len(req.transcript_blocks)
    failed_blocks: List[Dict[str, Any]] = []
    enhance_non_empty    = sum(1 for b in req.transcript_blocks if (b.get("transcript") or "").strip())
    estimated_enhance_seconds = estimate_gemini_enhance_wall_seconds(enhance_non_empty)
    t_enhance0 = time.perf_counter()

    def process_block(index: int, block: Dict[str, Any]):
        text       = (block.get("transcript") or "").strip()
        speakers   = block.get("speakers", [])
        timestamps = block.get("timestamps", [])
        if not text:
            return index, block, None

        speaker  = speakers[0] if speakers else "S0"
        gender   = req.speaker_genders.get(speaker, "unknown")
        start_s  = float(timestamps[0]) if isinstance(timestamps, list) and len(timestamps) > 0 else 0.0
        end_s    = float(timestamps[1]) if isinstance(timestamps, list) and len(timestamps) > 1 else (start_s + 3.0)
        if end_s <= start_s:
            end_s = start_s + 3.0

        try:
            chunk_b64 = extract_audio_chunk_base64(audio_path, start_s, end_s)
            refined   = call_gemini_with_retry(
                text=text,
                audio_chunk_b64=chunk_b64,
                speaker_label=speaker,
                speaker_gender=gender,
                source_lang=resolved_source_lang,
                target_lang=resolved_target_lang,
            )
            emotion_tags, cleaned_refined = extract_emotion_tags_and_clean_text(refined)
            updated = {
                **block,
                "transcript":   cleaned_refined or text,
                "emotion_tags": emotion_tags,
            }
            return index, updated, None
        except Exception as exc:
            return index, block, str(exc)

    with ThreadPoolExecutor(max_workers=GEMINI_MAX_WORKERS) as executor:
        futures = [executor.submit(process_block, i, b) for i, b in enumerate(req.transcript_blocks)]
        for future in as_completed(futures):
            idx, out_block, err = future.result()
            enhanced_blocks[idx] = out_block
            if err:
                failed_blocks.append({"index": idx, "error": err})

    enhance_processing_seconds = time.perf_counter() - t_enhance0

    return JSONResponse(content={
        "blocks": enhanced_blocks,
        "failed_block_count": len(failed_blocks),
        "failed_blocks": failed_blocks,
        "estimated_enhance_seconds": round(estimated_enhance_seconds, 1),
        "enhance_processing_seconds": round(enhance_processing_seconds, 2),
    })


# ── Gemini session preview (for manual mode) ──────────────────────────────────

@app.post("/api/gemini/session-preview")
async def gemini_session_preview(req: GeminiSessionPreviewRequest):
    """Preview Gemini speaker-wise transcript for a session without synthesis."""
    session_dir = os.path.join(TEMP_DIR, req.session_id)
    audio_path  = os.path.join(session_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Session audio not found.")
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    from pathlib import Path
    from google import genai as _genai

    try:
        client   = _genai.Client(api_key=GEMINI_API_KEY)
        analysis = await asyncio.to_thread(gemini_seg.analyse_audio, client, Path(audio_path), GEMINI_MODEL)
        source_lang = analysis.get("language", "en-US")
        target_lang = (req.target_lang or "").strip()
        if target_lang and target_lang != source_lang:
            await asyncio.to_thread(
                gemini_seg.translate_segments_in_place,
                client, analysis, target_lang, GEMINI_MODEL,
            )

        segments = analysis.get("segments", []) or []
        speakers = analysis.get("speakers", []) or []
        blocks = [
            {
                "id": f"gemini-preview-{i}",
                "speakers": [seg.get("speaker", "S0")],
                "transcript": seg.get("tagged_text") or seg.get("text") or "",
                "timestamps": [
                    float(seg.get("start", 0.0) or 0.0),
                    float(seg.get("end", 0.0) or 0.0),
                ],
            }
            for i, seg in enumerate(segments)
        ]

        return JSONResponse(content={
            "blocks": blocks,
            "speakers": speakers,
            "source_language": source_lang,
            "target_language": target_lang or source_lang,
            "num_speakers": analysis.get("num_speakers", len(speakers) or 1),
        })
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Gemini preview failed: {exc}")


# ── Stage 4: Synthesize (batched-per-speaker Gemini TTS) ──────────────────────

@app.post("/api/synthesize")
async def synthesize_audio(req: SynthesisRequest):
    """Run the batched-per-speaker Gemini TTS pipeline.

    Flow:
      1. Group segments by speaker.
      2. Send ONE TTS request per speaker (all speakers in parallel).
      3. Split each speaker's audio blob back into per-segment clips via
         silence detection / duration-guided slicing.
      4. Overlay every clip onto a silent timeline at its original timestamp.
      5. Mux onto the original video if available.

    For N speakers in a 1-minute video → N API calls, not M (segment count).
    """
    cleanup_expired_sessions()

    if req.session_id and os.path.exists(os.path.join(TEMP_DIR, req.session_id)):
        session_id  = req.session_id
        session_dir = os.path.join(TEMP_DIR, session_id)
    else:
        session_id  = str(uuid.uuid4())
        session_dir = os.path.join(TEMP_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)

    try:
        voice_map_dict = {m.speaker_id: m.voice_id for m in req.voice_map}
        payload = await asyncio.to_thread(
            _synthesize_batched_per_speaker_sync,
            session_dir,
            session_id,
            req.target_lang,
            req.target_duration_ms,
            GEMINI_MODEL,
            req.transcript_blocks if not req.auto_detect_speakers else None,
            voice_map_dict,
            req.speaker_genders,
            not req.auto_detect_speakers,
        )
        return JSONResponse(content=payload)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Direct ElevenLabs end-to-end dub endpoints ───────────────────────────────

@app.get("/api/dub-direct/languages")
async def dub_direct_languages():
    return JSONResponse(content={"languages": ELEVEN_DIRECT_DUB_LANGUAGES})


@app.post("/api/dub-direct/start")
async def dub_direct_start(
    file: UploadFile = File(...),
    target_lang: str = Form(...),
    source_lang: str = Form("auto"),
    num_speakers: int = Form(0),
    disable_voice_cloning: bool = Form(False),
):
    cleanup_expired_sessions()

    session_id  = str(uuid.uuid4())
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    original_filename = file.filename or "input.mp4"
    ext = os.path.splitext(original_filename)[1].lower() or ".mp4"
    file_path = os.path.join(session_dir, f"input{ext}")

    try:
        with open(file_path, "wb") as out:
            while True:
                chunk = await file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {str(e)}")

    try:
        create_res = await asyncio.to_thread(
            eleven_post_dubbing,
            file_path=file_path,
            target_lang=target_lang,
            source_lang=source_lang,
            num_speakers=num_speakers,
            disable_voice_cloning=disable_voice_cloning,
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create dubbing job: {str(e)}")

    dubbing_id = create_res.get("dubbing_id")
    if not dubbing_id:
        raise HTTPException(status_code=500, detail=f"Invalid ElevenLabs response: {create_res}")

    meta = {
        "dubbing_id": dubbing_id,
        "target_lang": target_lang,
        "source_lang": source_lang,
        "file_path": file_path,
        "is_video": ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"),
    }
    with open(os.path.join(session_dir, "meta.json"), "w") as mf:
        json.dump(meta, mf)

    return JSONResponse(content={
        "session_id": session_id,
        "dubbing_id": dubbing_id,
        "status": "pending",
    })


@app.get("/api/dub-direct/status/{dubbing_id}")
async def dub_direct_status(dubbing_id: str):
    try:
        status_payload = await asyncio.to_thread(eleven_get_dubbing, dubbing_id)
        return JSONResponse(content={
            "dubbing_id": dubbing_id,
            "status": status_payload.get("status", "unknown"),
            "expected_duration_sec": status_payload.get("expected_duration_sec"),
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _dub_direct_finalize_sync(session_id: str, dubbing_id: str, target_lang: str) -> Dict[str, Any]:
    session_dir = os.path.join(TEMP_DIR, session_id)
    if not os.path.isdir(session_dir):
        raise HTTPException(status_code=404, detail="Session not found.")

    meta_path = os.path.join(session_dir, "meta.json")
    is_video  = False
    if os.path.exists(meta_path):
        with open(meta_path) as mf:
            meta = json.load(mf)
        is_video = meta.get("is_video", False)

    dubbed_bytes = eleven_download_dubbed_file(dubbing_id, target_lang)
    audio_url = None
    video_url = None

    if is_video:
        final_mp4 = os.path.join(session_dir, "final.mp4")
        with open(final_mp4, "wb") as f:
            f.write(dubbed_bytes)
        video_url = f"/api/video/{session_id}"
        output_wav = os.path.join(session_dir, "final.wav")
        cmd = ["ffmpeg", "-y", "-i", final_mp4, "-vn", "-acodec", "pcm_s16le", output_wav]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            audio_url = f"/api/audio/{session_id}"
    else:
        output_mp3 = os.path.join(session_dir, "dubbed.mp3")
        with open(output_mp3, "wb") as f:
            f.write(dubbed_bytes)
        output_wav = os.path.join(session_dir, "final.wav")
        cmd = ["ffmpeg", "-y", "-i", output_mp3, "-acodec", "pcm_s16le", output_wav]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            audio_url = f"/api/audio/{session_id}"

    return {"audio_url": audio_url, "video_url": video_url, "dubbing_id": dubbing_id}


@app.post("/api/dub-direct/finalize")
async def dub_direct_finalize(req: DirectDubFinalizeRequest):
    try:
        result = await asyncio.to_thread(
            _dub_direct_finalize_sync,
            req.session_id,
            req.dubbing_id,
            req.target_lang,
        )
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Media serving ─────────────────────────────────────────────────────────────

@app.get("/api/audio/{session_id}")
async def get_audio(session_id: str):
    cleanup_expired_sessions()
    file_path = os.path.join(TEMP_DIR, session_id, "final.wav")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="audio/wav")
    raise HTTPException(status_code=404, detail="Audio not found")


@app.get("/api/video/{session_id}")
async def get_video(session_id: str):
    cleanup_expired_sessions()
    file_path = os.path.join(TEMP_DIR, session_id, "final.mp4")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4")
    raise HTTPException(status_code=404, detail="Video not found")