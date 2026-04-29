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

from sarvamai import SarvamAI
from pydub import AudioSegment
from dotenv import load_dotenv

# The N-speaker single-TTS pipeline lives in scratch/tts_experiment.py — we
# import it as a module so the API can reuse the exact same primitives that
# the CLI script uses (no logic duplication).
_SCRATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch")
if _SCRATCH_DIR not in sys.path:
    sys.path.insert(0, _SCRATCH_DIR)
import tts_experiment as gemini_seg  # type: ignore[import-not-found]

# Load env variables
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Clients
SARVAM_KEY = os.getenv("SARVAM_API_KEY")
ELEVEN_KEY = os.getenv("ELEVEN_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

sarvam_client = SarvamAI(api_subscription_key=SARVAM_KEY)

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(WORKSPACE_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)
TEMP_RETENTION_HOURS = int(os.getenv("TEMP_RETENTION_HOURS", "24"))
SYNTHESIS_MAX_WORKERS = int(os.getenv("SYNTHESIS_MAX_WORKERS", "2"))
SYNTHESIS_RETRY_ATTEMPTS = int(os.getenv("SYNTHESIS_RETRY_ATTEMPTS", "4"))
SYNTHESIS_RETRY_BASE_SECONDS = float(os.getenv("SYNTHESIS_RETRY_BASE_SECONDS", "1.0"))
TRANSLATE_MAX_WORKERS = int(os.getenv("TRANSLATE_MAX_WORKERS", "4"))
TRANSLATE_RETRY_ATTEMPTS = int(os.getenv("TRANSLATE_RETRY_ATTEMPTS", "3"))
TRANSLATE_RETRY_BASE_SECONDS = float(os.getenv("TRANSLATE_RETRY_BASE_SECONDS", "1.0"))
# Rough wall-clock heuristics for UI ETA (tune via env for your Sarvam account/latency).
STT_SECONDS_PER_MINUTE_AUDIO = float(os.getenv("STT_SECONDS_PER_MINUTE_AUDIO", "35"))
STT_MIN_ESTIMATE_SECONDS = float(os.getenv("STT_MIN_ESTIMATE_SECONDS", "20"))
TRANSLATE_SECONDS_PER_BLOCK = float(os.getenv("TRANSLATE_SECONDS_PER_BLOCK", "0.9"))
GEMINI_SECONDS_PER_BLOCK = float(os.getenv("GEMINI_SECONDS_PER_BLOCK", "4.0"))
ELEVEN_BASE_URL = os.getenv("ELEVEN_BASE_URL", "https://api.elevenlabs.io")
ELEVEN_DUBBING_POLL_INTERVAL_SECONDS = float(os.getenv("ELEVEN_DUBBING_POLL_INTERVAL_SECONDS", "4"))
ELEVEN_DUBBING_TIMEOUT_SECONDS = int(os.getenv("ELEVEN_DUBBING_TIMEOUT_SECONDS", "600"))
ELEVEN_SUPPORTED_TARGET_LANGS = os.getenv(
    "ELEVEN_SUPPORTED_TARGET_LANGS",
    "hi-IN,bn-IN,ta-IN,te-IN,kn-IN,ml-IN,gu-IN,pa-IN,od-IN,mr-IN,en-IN",
)
SARVAM_FALLBACK_SPEAKER = os.getenv("SARVAM_FALLBACK_SPEAKER", "priya")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
GEMINI_MAX_WORKERS = int(os.getenv("GEMINI_MAX_WORKERS", "1"))
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "5"))
GEMINI_RETRY_BASE_SECONDS = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "2.0"))
GEMINI_RETRY_MAX_SECONDS = float(os.getenv("GEMINI_RETRY_MAX_SECONDS", "45.0"))
GEMINI_MIN_LENGTH_RATIO = float(os.getenv("GEMINI_MIN_LENGTH_RATIO", "0.75"))

# -----------------
# Pydantic Models
# -----------------
class TranslateRequest(BaseModel):
    transcript_blocks: List[Dict[str, Any]]
    target_lang: str
    source_lang: str = "en-IN"
    # Optional map e.g. {"S1": "Male", "S2": "Male"} — Sarvam uses this for agreement (रहा vs रही) when English is ambiguous.
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
    target_lang: str = 'hi-IN'
    auto_detect_speakers: bool = True
    disable_voice_cloning: bool = False
    synthesis_mode: str = "dubbing_auto"


class EnhanceTranscriptRequest(BaseModel):
    session_id: str
    transcript_blocks: List[Dict[str, Any]]
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None
    speaker_genders: Dict[str, str] = {}


class GeminiSessionPreviewRequest(BaseModel):
    session_id: str
    target_lang: Optional[str] = None

# -----------------
# Helper Functions
# -----------------
def cleanup_expired_sessions() -> int:
    """Delete session directories older than configured retention window."""
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
    # Using ffmpeg exactly as requested
    command = [
        "ffmpeg", "-y", "-i", video_path, 
        "-vn", "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2", "-b:a", "192k", 
        audio_path
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def normalize_lang_code(lang: str) -> str:
    """Map locale-like codes (hi-IN) to ISO code (hi) for ElevenLabs."""
    if not lang:
        return "auto"
    return lang.split("-")[0].lower()


def normalize_sarvam_lang_code(lang: str) -> str:
    """Use a safe Sarvam language default for text-driven synthesis."""
    return lang or "hi-IN"


def eleven_headers() -> Dict[str, str]:
    if not ELEVEN_KEY:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY is not configured.")
    return {"xi-api-key": ELEVEN_KEY}


def eleven_post_dubbing(
    file_path: str,
    target_lang: str,
    source_lang: str = "auto",
    num_speakers: int = 0,
    disable_voice_cloning: bool = False,
) -> Dict[str, Any]:
    boundary = f"----CursorBoundary{uuid.uuid4().hex}"
    filename = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(file_path, "rb") as fh:
        file_data = fh.read()

    parts = []
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
    )
    parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    parts.append(file_data)
    parts.append(b"\r\n")
    for key, value in [
        ("target_lang", target_lang),
        ("source_lang", source_lang),
        ("num_speakers", num_speakers),
        ("disable_voice_cloning", str(disable_voice_cloning).lower()),
    ]:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
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
        print(f"ElevenLabs get dubbing status failed [{exc.code}] for {dubbing_id}: {detail}")
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
        print(f"ElevenLabs download dubbed file failed [{exc.code}] for {dubbing_id}/{language_code}: {detail}")
        raise HTTPException(status_code=exc.code, detail=f"ElevenLabs dubbed file download failed: {detail}")


def generate_voice_clip_sarvam(text: str, speaker: str, target_lang: str, output_path: str) -> AudioSegment:
    """Generate per-block audio from text for experiment mode."""
    res = sarvam_client.text_to_speech.convert(
        text=text,
        target_language_code=normalize_sarvam_lang_code(target_lang),
        speaker=speaker,
        model="bulbul:v3",
        speech_sample_rate=48000,
        output_audio_codec="wav",
    )
    if not getattr(res, "audios", None):
        raise ValueError("Sarvam TTS returned empty audio")
    raw_bytes = base64.b64decode(res.audios[0])
    audio_seg = AudioSegment.from_wav(io.BytesIO(raw_bytes))
    audio_seg.export(output_path, format="wav")
    return audio_seg


def normalize_sarvam_translate_gender(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    r = raw.strip().lower()
    if r in ("male", "m", "man"):
        return "Male"
    if r in ("female", "f", "woman"):
        return "Female"
    return None


def translate_text_with_retry(
    text: str,
    source_lang: str,
    target_lang: str,
    speaker_gender: Optional[str] = None,
) -> str:
    """Retry translate on transient server/rate-limit errors."""
    gender = normalize_sarvam_translate_gender(speaker_gender)
    for attempt in range(TRANSLATE_RETRY_ATTEMPTS):
        try:
            kwargs: Dict[str, Any] = {
                "input": text,
                "source_language_code": source_lang,
                "target_language_code": target_lang,
                "model": "sarvam-translate:v1",
            }
            if gender:
                kwargs["speaker_gender"] = gender
            res = sarvam_client.text.translate(**kwargs)
            return res.translated_text
        except Exception as exc:
            err = str(exc).lower()
            is_retryable = (
                "500" in err
                or "internal_server_error" in err
                or "429" in err
                or "rate limit" in err
                or "rate_limit_exceeded_error" in err
                or "timeout" in err
            )
            last_attempt = attempt == TRANSLATE_RETRY_ATTEMPTS - 1
            if not is_retryable or last_attempt:
                raise
            sleep_seconds = TRANSLATE_RETRY_BASE_SECONDS * (2 ** attempt)
            print(
                f"Translate transient error. "
                f"Retry {attempt + 1}/{TRANSLATE_RETRY_ATTEMPTS} in {sleep_seconds:.1f}s."
            )
            time.sleep(sleep_seconds)


def normalize_translate_source_lang(source_lang: str) -> str:
    """Sarvam translate does not accept 'auto' as source language code."""
    if not source_lang or source_lang.lower() == "auto":
        return "hi-IN"
    return source_lang


def get_audio_duration_seconds_ffprobe(path: str) -> float:
    """Lightweight duration via ffprobe (avoids decoding full file in Python)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
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


def _upload_extract_and_stt_sync(
    video_path: str,
    audio_path: str,
    language_code: Optional[str],
) -> Dict[str, Any]:
    """Blocking: ffmpeg extract, duration, Sarvam STT. Runs in a thread pool."""
    try:
        t_extract0 = time.perf_counter()
        extract_audio(video_path, audio_path)
        audio_extract_seconds = time.perf_counter() - t_extract0
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Audio extraction failed: {str(e)}") from e

    audio_duration_seconds = get_audio_duration_seconds(audio_path)
    estimated_stt_seconds = estimate_stt_wall_seconds(audio_duration_seconds)

    try:
        t_stt0 = time.perf_counter()
        job = sarvam_client.speech_to_text_job.create_job(
            model="saaras:v3",
            mode="transcribe",
            with_diarization=True,
            with_timestamps=True,
            language_code=language_code if language_code else None,
        )
        job.upload_files(file_paths=[audio_path])
        job.start()
        job.wait_until_complete()

        session_dir = os.path.dirname(video_path)
        output_folder = os.path.join(session_dir, "results")
        os.makedirs(output_folder, exist_ok=True)
        job.download_outputs(output_dir=output_folder)
        stt_job_seconds = time.perf_counter() - t_stt0

        result_file = os.path.join(output_folder, "audio.mp3.json")
        with open(result_file, "r", encoding="utf-8") as rf:
            data = json.load(rf)

        return {
            "data": data,
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


def estimate_stt_wall_seconds(audio_duration_sec: float) -> float:
    """Heuristic wall time for Sarvam batch STT (diarized) vs. audio length."""
    if audio_duration_sec <= 0:
        return STT_MIN_ESTIMATE_SECONDS
    return max(STT_MIN_ESTIMATE_SECONDS, (audio_duration_sec / 60.0) * STT_SECONDS_PER_MINUTE_AUDIO)


def count_translate_api_calls(
    transcript_blocks: List[Dict[str, Any]], target_lang: str, resolved_source_lang: str
) -> int:
    n = 0
    for block in transcript_blocks:
        text = (block.get("transcript") or "").strip()
        if not text:
            continue
        if target_lang != resolved_source_lang:
            n += 1
    return n


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


def extract_audio_chunk_base64(audio_path: str, start_s: float, end_s: float, max_seconds: float = 8.0) -> str:
    """Extract a short WAV chunk and return base64 payload."""
    full_audio = AudioSegment.from_file(audio_path)
    start_ms = max(0, int(start_s * 1000))
    end_ms = max(start_ms, int(end_s * 1000))
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
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": audio_chunk_b64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.4
        }
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
        if exc.code == 403 and "unregistered callers" in detail.lower():
            raise HTTPException(
                status_code=403,
                detail=(
                    "Gemini key is invalid for this API. "
                    "Set a valid Google AI Studio API key in GEMINI_API_KEY "
                    "and restart the backend."
                ),
            )
        raise HTTPException(status_code=exc.code, detail=f"Gemini enhancement failed: {detail}")

    candidates = data.get("candidates") or []
    if not candidates:
        return text
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    merged_text = " ".join((p.get("text") or "").strip() for p in parts if p.get("text")).strip()
    if not merged_text:
        return text

    # Guard: for longer lines, reject overly shortened rewrites (likely meaning drop).
    src = text.strip()
    out = merged_text.strip()
    if len(src) >= 80:
        src_len = max(1, len(src))
        length_ratio = len(out) / src_len
        src_clauses = len(re.findall(r"[,.!?;:]", src))
        out_clauses = len(re.findall(r"[,.!?;:]", out))
        if length_ratio < GEMINI_MIN_LENGTH_RATIO or (src_clauses >= 2 and out_clauses < max(1, src_clauses - 1)):
            print(
                f"[Gemini guard] fallback to source line due to compression: "
                f"ratio={length_ratio:.2f}, src_clauses={src_clauses}, out_clauses={out_clauses}"
            )
            return text

    return merged_text


def infer_retry_delay_seconds(error_text: str) -> Optional[float]:
    """Extract retry delay from Gemini error strings when present."""
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
    """Extract [tag] tokens and return cleaned text for TTS."""
    if not text:
        return [], ""
    tags = [m.strip() for m in re.findall(r"\[([^\[\]]+)\]", text) if m.strip()]
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
    """Retry Gemini enhancement for transient overload/quota windows."""
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
            detail_text = str(exc.detail or "")
            # Retry only for known transient Gemini conditions.
            if exc.status_code not in (429, 503):
                raise
            if attempt >= GEMINI_RETRY_ATTEMPTS:
                raise
            retry_after = infer_retry_delay_seconds(detail_text)
            if retry_after is None:
                retry_after = min(GEMINI_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), GEMINI_RETRY_MAX_SECONDS)
            # Small buffer to avoid thundering herd when many requests fail together.
            sleep_seconds = min(retry_after + 0.25, GEMINI_RETRY_MAX_SECONDS)
            print(
                f"[Gemini retry] attempt={attempt}/{GEMINI_RETRY_ATTEMPTS} "
                f"status={exc.status_code} wait={sleep_seconds:.2f}s speaker={speaker_label}"
            )
            time.sleep(sleep_seconds)
        except Exception as exc:
            last_exc = exc
            if attempt >= GEMINI_RETRY_ATTEMPTS:
                raise
            backoff = min(GEMINI_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), GEMINI_RETRY_MAX_SECONDS)
            time.sleep(backoff)
    if last_exc:
        raise last_exc
    return text


# ----------------------------------------------------------------
# N-speaker single-TTS pipeline (powered by scratch/tts_experiment)
# ----------------------------------------------------------------

def _synthesize_per_segment_gemini_sync(
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
    """Run the full N-speaker single-TTS pipeline for one session.

    Steps:
      1. Gemini analysis on the session's audio.mp3 — emits N speakers +
         per-segment timestamps + tagged_text.
      2. Per-segment translation if target_lang != source_lang.
      3. Per-segment single-speaker TTS, each clip speed-matched to its
         own (end - start) window.
      4. Overlay every clip onto a silent timeline at its original
         start time -> final.wav.
      5. Mux final.wav onto input.mp4 if present -> final.mp4.

    Runs synchronously (long-running). Call via asyncio.to_thread.
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

    def _build_manual_analysis_from_blocks(
        blocks: List[Dict[str, Any]],
        voice_map: Dict[str, str],
        speaker_genders: Dict[str, str],
    ) -> Dict[str, Any]:
        segments: List[Dict[str, Any]] = []
        speakers: Dict[str, Dict[str, Any]] = {}
        for idx, block in enumerate(blocks):
            text = (block.get("transcript") or "").strip()
            if not text:
                continue
            raw_speakers = block.get("speakers") or []
            spk = str(raw_speakers[0]) if raw_speakers else f"S{idx+1}"
            ts = block.get("timestamps") or []
            start_s = float(ts[0]) if isinstance(ts, list) and len(ts) > 0 else float(idx * 2)
            end_s = float(ts[1]) if isinstance(ts, list) and len(ts) > 1 else (start_s + 2.0)
            if end_s <= start_s:
                end_s = start_s + 0.5
            voice = (voice_map.get(spk) or "").strip()
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
        full_text = " ".join(seg.get("text", "") for seg in segments).strip()
        tagged = "\n".join(f"{seg['speaker']}: {seg['tagged_text']}" for seg in segments)
        return {
            "transcript": full_text,
            "num_speakers": len(speakers) or 1,
            "speakers": list(speakers.values()),
            "segments": segments,
            "tagged_transcript": tagged,
            "language": "unknown",
            "pace": "moderate",
        }

    # Step 1: analyse audio (auto) OR use manual transcript/timeline.
    if manual_mode and manual_blocks:
        analysis = _build_manual_analysis_from_blocks(
            manual_blocks,
            manual_voice_map or {},
            manual_speaker_genders or {},
        )
    else:
        analysis = gemini_seg.analyse_audio(client, Path(audio_path), analysis_model)
    num_speakers = analysis.get("num_speakers", 1)
    segments = analysis.get("segments", [])
    if not segments:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Gemini analysis returned no 'segments' field "
                f"(num_speakers={num_speakers}). The analysis model may not "
                f"have followed the new schema. Try a different --model."
            ),
        )

    # Step 2: translate per-segment if needed.
    is_dubbing = bool(
        target_lang and target_lang != analysis.get("language", "en-US")
    )
    if is_dubbing:
        gemini_seg.translate_segments_in_place(client, analysis, target_lang, analysis_model)

    # Save the analysis (with VROTT speaker plan + dubbed text) for inspection.
    analysis_to_save = dict(analysis)
    analysis_to_save["speaker_plan"] = gemini_seg.build_speaker_plan(analysis)
    analysis_to_save["multi_speaker_mode_used"] = "single-per-segment"
    analysis_to_save["manual_mode"] = bool(manual_mode and manual_blocks)
    if is_dubbing:
        analysis_to_save["dubbed_language"] = target_lang
    with open(os.path.join(session_dir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(analysis_to_save, f, indent=2, ensure_ascii=False)

    # Step 3+4: per-segment TTS + timeline overlay.
    audio_out = Path(session_dir) / "final.wav"

    # Determine timeline length: prefer explicit target, else source duration.
    if target_duration_ms and target_duration_ms > 0:
        target_dur_s: Optional[float] = float(target_duration_ms) / 1000.0
    else:
        input_video = os.path.join(session_dir, "input.mp4")
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

    # Step 5: mux back onto the original video if available.
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
            print(f"[SegmentDub] FFmpeg muxing error: {result.stderr[:500]}")

    return {
        "audio_url": f"/api/audio/{session_id}",
        "video_url": final_video_url,
        "provider": "gemini_single_per_segment",
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


# -----------------
# API Endpoints
# -----------------

@app.get("/api/config")
async def get_config():
    supported_langs = [code.strip() for code in ELEVEN_SUPPORTED_TARGET_LANGS.split(",") if code.strip()]
    return JSONResponse(content={
        "supported_target_languages": supported_langs,
        "timing": {
            "translate_max_workers": TRANSLATE_MAX_WORKERS,
            "translate_seconds_per_block": TRANSLATE_SECONDS_PER_BLOCK,
            "stt_seconds_per_minute_audio": STT_SECONDS_PER_MINUTE_AUDIO,
            "stt_min_estimate_seconds": STT_MIN_ESTIMATE_SECONDS,
            "gemini_max_workers": GEMINI_MAX_WORKERS,
            "gemini_seconds_per_block": GEMINI_SECONDS_PER_BLOCK,
        },
    })

@app.post("/api/upload-fast")
async def process_upload_fast(file: UploadFile = File(...)):
    """Upload + extract audio.mp3 ONLY — no Sarvam STT, no diarization.

    Used by the Gemini per-segment dub flow: Gemini transcribes/diarizes the
    audio itself during synthesis, so paying Sarvam for the same work is
    wasteful and slow. This endpoint just lands the file + audio.mp3 in the
    session dir and returns the session_id, ready for /api/synthesize with
    synthesis_mode='single_per_segment_gemini'.
    """
    cleanup_expired_sessions()
    if not file.filename.endswith(('.mp4', '.mov', '.avi')):
        raise HTTPException(status_code=400, detail="Invalid file format")

    session_id = str(uuid.uuid4())
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


@app.post("/api/upload")
async def process_upload(
    file: UploadFile = File(...),
    language_code: Optional[str] = Form(None)
):
    cleanup_expired_sessions()
    if not file.filename.endswith(('.mp4', '.mov', '.avi')):
        raise HTTPException(status_code=400, detail="Invalid file format")

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    video_path = os.path.join(session_dir, "input.mp4")
    audio_path = os.path.join(session_dir, "audio.mp3")

    # Stream to disk so we do not block the event loop on the whole body at once.
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

    # Long ffmpeg + Sarvam STT must not run on the asyncio thread (avoids stalls / client resets).
    try:
        payload = await asyncio.to_thread(
            _upload_extract_and_stt_sync, video_path, audio_path, language_code
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Upload pipeline failed: {str(e)}") from e

    return JSONResponse(
        content={
            "session_id": session_id,
            "message": "Upload & Diarization successful",
            **payload,
        }
    )


@app.post("/api/translate")
async def translate_text(req: TranslateRequest):
    translated_blocks = [None] * len(req.transcript_blocks)
    failed_blocks = []
    resolved_source_lang = normalize_translate_source_lang(req.source_lang)
    translate_api_calls = count_translate_api_calls(
        req.transcript_blocks, req.target_lang, resolved_source_lang
    )
    estimated_translate_seconds = estimate_translate_wall_seconds(translate_api_calls)
    t_translate0 = time.perf_counter()

    gender_map = req.speaker_genders or {}

    def process_block(index, block):
        speakers = block.get('speakers', [])
        timestamps = block.get('timestamps', [])
        original_text = block.get('transcript', '')
        primary_speaker = str(speakers[0]) if speakers else ""
        block_gender = None
        if primary_speaker:
            block_gender = gender_map.get(primary_speaker) or gender_map.get(primary_speaker.upper())

        # Translate the snippet using Sarvam AI
        try:
            if original_text.strip() and req.target_lang != resolved_source_lang:
                print(f"Translating block from {resolved_source_lang} to {req.target_lang}: {original_text[:50]}...")
                trans_text = translate_text_with_retry(
                    original_text,
                    resolved_source_lang,
                    req.target_lang,
                    speaker_gender=block_gender,
                )
            else:
                trans_text = original_text
            error_text = None
        except Exception as e:
            print(f"Translation Error for block: {original_text[:50]}")
            traceback.print_exc()
            trans_text = original_text # Keep original on error instead of error message
            error_text = str(e)
        
        return index, {
            "transcript": trans_text,
            "speakers": speakers,
            "timestamps": timestamps
        }, error_text

    with ThreadPoolExecutor(max_workers=TRANSLATE_MAX_WORKERS) as executor:
        futures = [executor.submit(process_block, i, block) for i, block in enumerate(req.transcript_blocks)]
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
    })


@app.post("/api/enhance-translation")
async def enhance_translation(req: EnhanceTranscriptRequest):
    session_dir = os.path.join(TEMP_DIR, req.session_id)
    audio_path = os.path.join(session_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Original session audio not found for enhancement.")

    resolved_source_lang = req.source_lang or "unknown"
    resolved_target_lang = req.target_lang or "unknown"
    enhanced_blocks = [None] * len(req.transcript_blocks)
    failed_blocks = []
    enhance_non_empty = sum(1 for b in req.transcript_blocks if (b.get("transcript") or "").strip())
    estimated_enhance_seconds = estimate_gemini_enhance_wall_seconds(enhance_non_empty)
    t_enhance0 = time.perf_counter()

    def process_block(index: int, block: Dict[str, Any]):
        text = (block.get("transcript") or "").strip()
        speakers = block.get("speakers", [])
        timestamps = block.get("timestamps", [])
        if not text:
            return index, block, None

        speaker = speakers[0] if speakers else "S0"
        gender = req.speaker_genders.get(speaker, "unknown")
        start_s = float(timestamps[0]) if isinstance(timestamps, list) and len(timestamps) > 0 else 0.0
        end_s = float(timestamps[1]) if isinstance(timestamps, list) and len(timestamps) > 1 else (start_s + 3.0)
        if end_s <= start_s:
            end_s = start_s + 3.0

        try:
            chunk_b64 = extract_audio_chunk_base64(audio_path, start_s, end_s)
            refined = call_gemini_with_retry(
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
                "transcript": cleaned_refined or text,
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


@app.post("/api/gemini/session-preview")
async def gemini_session_preview(req: GeminiSessionPreviewRequest):
    """Preview Gemini speaker-wise transcript for a session without synthesis.

    This is used by Stage 3 manual mode so users can first see auto-detected
    speaker turns/timestamps, then edit transcript/timeline/gender/voice
    before final synthesis.
    """
    session_dir = os.path.join(TEMP_DIR, req.session_id)
    audio_path = os.path.join(session_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Session audio not found.")
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    from pathlib import Path
    from google import genai as _genai

    try:
        client = _genai.Client(api_key=GEMINI_API_KEY)
        analysis = await asyncio.to_thread(gemini_seg.analyse_audio, client, Path(audio_path), GEMINI_MODEL)
        source_lang = analysis.get("language", "en-US")
        target_lang = (req.target_lang or "").strip()
        if target_lang and target_lang != source_lang:
            await asyncio.to_thread(
                gemini_seg.translate_segments_in_place,
                client,
                analysis,
                target_lang,
                GEMINI_MODEL,
            )

        segments = analysis.get("segments", []) or []
        speakers = analysis.get("speakers", []) or []
        blocks = []
        for i, seg in enumerate(segments):
            blocks.append({
                "id": f"gemini-preview-{i}",
                "speakers": [seg.get("speaker", "S0")],
                "transcript": seg.get("tagged_text") or seg.get("text") or "",
                "timestamps": [float(seg.get("start", 0.0) or 0.0), float(seg.get("end", 0.0) or 0.0)],
            })

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


@app.post("/api/synthesize")
async def synthesize_audio(req: SynthesisRequest):
    cleanup_expired_sessions()
    if req.session_id and os.path.exists(os.path.join(TEMP_DIR, req.session_id)):
        session_id = req.session_id
        session_dir = os.path.join(TEMP_DIR, session_id)
    else:
        session_id = str(uuid.uuid4())
        session_dir = os.path.join(TEMP_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        
    with open(os.path.join(TEMP_DIR, "debug_log.txt"), "a") as f:
        f.write(f"Synthesize Called. Received session_id: {req.session_id}. Resolved to: {session_id}\n")
    
    try:
        if req.synthesis_mode == "text_experiment":
            if req.target_duration_ms > 0:
                target_ms = int(req.target_duration_ms)
            else:
                target_ms = 0
                for block in req.transcript_blocks:
                    timestamps = block.get('timestamps', [])
                    if timestamps and len(timestamps) > 1:
                        end_time_ms = float(timestamps[1]) * 1000
                        if end_time_ms > target_ms:
                            target_ms = end_time_ms
                target_ms = int(target_ms) + 5000

            mixed_audio = AudioSegment.silent(duration=target_ms)
            voice_map_dict = {m.speaker_id: m.voice_id for m in req.voice_map}
            failed_blocks = []

            # Pre-compute all block start times so we can derive each block's
            # available time window and hard-clip synthesized audio to it.
            block_start_times_ms = []
            for block in req.transcript_blocks:
                ts = block.get("timestamps", [])
                start_ms = float(ts[0]) * 1000 if isinstance(ts, list) and ts else 0.0
                block_start_times_ms.append(start_ms)

            for index, block in enumerate(req.transcript_blocks):
                text = (block.get("transcript") or "").strip()
                if not text:
                    continue

                speakers = block.get("speakers", [])
                timestamps = block.get("timestamps", [])
                primary_speaker = speakers[0] if speakers else "S0"
                sarvam_speaker = voice_map_dict.get(primary_speaker, SARVAM_FALLBACK_SPEAKER)
                if sarvam_speaker == "auto":
                    sarvam_speaker = SARVAM_FALLBACK_SPEAKER
                start_time_ms = block_start_times_ms[index]

                # Determine max allowed duration for this block:
                # It ends when the next block starts (or at the end of the track).
                if index + 1 < len(block_start_times_ms):
                    # Find the next block that actually has content
                    next_start_ms = block_start_times_ms[index + 1]
                else:
                    next_start_ms = float(target_ms)
                max_duration_ms = max(100, next_start_ms - start_time_ms)

                clip_path = os.path.join(session_dir, f"exp_clip_{index}.wav")
                try:
                    print(f"[Experiment] Synthesizing block {index}: {text[:40]}...")
                    audio_seg = generate_voice_clip_sarvam(text, sarvam_speaker, req.target_lang, clip_path)

                    clip_len_ms = len(audio_seg)
                    if clip_len_ms > max_duration_ms:
                        # Hard-clip the segment to the available window.
                        # Apply a short fade-out (up to 80 ms) to avoid a harsh cut.
                        fade_ms = min(80, max_duration_ms // 4)
                        audio_seg = audio_seg[:max_duration_ms].fade_out(fade_ms)
                        print(
                            f"[Experiment] Block {index} clipped from {clip_len_ms}ms "
                            f"to {max_duration_ms}ms to avoid overlap with next speaker."
                        )

                    mixed_audio = mixed_audio.overlay(audio_seg, position=int(start_time_ms))
                except Exception as exc:
                    print(f"[Experiment] Synthesis failed for block {index}: {exc}")
                    failed_blocks.append({"index": index, "error": str(exc)})

            output_wav = os.path.join(session_dir, "final.wav")
            mixed_audio.export(output_wav, format="wav")

            total_non_empty_blocks = sum(
                1 for block in req.transcript_blocks if (block.get("transcript") or "").strip()
            )
            if total_non_empty_blocks > 0 and len(failed_blocks) == total_non_empty_blocks:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "All text-experiment synthesis blocks failed. "
                        "Check speaker compatibility and language settings."
                    ),
                )

            final_video_url = None
            input_video = os.path.join(session_dir, "input.mp4")
            if os.path.exists(input_video):
                final_mp4 = os.path.join(session_dir, "final.mp4")
                cmd = [
                    "ffmpeg", "-y", "-i", input_video, "-i", output_wav,
                    "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                    final_mp4
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    final_video_url = f"/api/video/{session_id}"
                else:
                    print(f"[Experiment] FFmpeg muxing error: {result.stderr}")

            return JSONResponse(content={
                "audio_url": f"/api/audio/{session_id}",
                "video_url": final_video_url,
                "provider": "sarvam_text_experiment",
                "failed_block_count": len(failed_blocks),
                "failed_blocks": failed_blocks,
            })

        if req.synthesis_mode == "single_per_segment_gemini":
            # N-speaker (1, 2, 3, 4+) Gemini pipeline:
            # Gemini analyses the audio, gives per-segment timestamps + voice
            # assignments per speaker, we run single-speaker TTS per segment,
            # speed-match each clip, and overlay onto a silent timeline.
            voice_map_dict = {m.speaker_id: m.voice_id for m in req.voice_map}
            payload = await asyncio.to_thread(
                _synthesize_per_segment_gemini_sync,
                session_dir,
                session_id,
                req.target_lang,
                req.target_duration_ms,
                GEMINI_MODEL,
                req.transcript_blocks,
                voice_map_dict,
                req.speaker_genders,
                not req.auto_detect_speakers,
            )
            return JSONResponse(content=payload)

        input_video = os.path.join(session_dir, "input.mp4")
        input_audio = os.path.join(session_dir, "audio.mp3")
        source_media = input_video if os.path.exists(input_video) else input_audio
        if not os.path.exists(source_media):
            raise HTTPException(status_code=404, detail="Source media not found for dubbing.")

        target_lang = normalize_lang_code(req.target_lang)
        source_lang = "auto"
        if req.transcript_blocks:
            source_lang = "auto"

        manual_speaker_count = len([m for m in req.voice_map if m.voice_id != "auto"])
        num_speakers = 0 if req.auto_detect_speakers else max(manual_speaker_count, 1)

        warning_message = None
        language_used = target_lang
        try:
            create_res = eleven_post_dubbing(
                file_path=source_media,
                target_lang=target_lang,
                source_lang=source_lang,
                num_speakers=num_speakers,
                disable_voice_cloning=req.disable_voice_cloning,
            )
        except HTTPException as exc:
            # If requested language is unsupported by ElevenLabs, fallback to English.
            if exc.status_code == 400 and is_unsupported_target_language_error(str(exc.detail)):
                language_used = "en"
                warning_message = (
                    f"Target language '{target_lang}' is not supported by ElevenLabs dubbing. "
                    "Used fallback language 'en'."
                )
                create_res = eleven_post_dubbing(
                    file_path=source_media,
                    target_lang=language_used,
                    source_lang=source_lang,
                    num_speakers=num_speakers,
                    disable_voice_cloning=req.disable_voice_cloning,
                )
            else:
                raise
        dubbing_id = create_res.get("dubbing_id")
        if not dubbing_id:
            raise HTTPException(status_code=500, detail=f"Invalid ElevenLabs response: {create_res}")

        deadline = time.time() + ELEVEN_DUBBING_TIMEOUT_SECONDS
        status_payload = {}
        while time.time() < deadline:
            status_payload = eleven_get_dubbing(dubbing_id)
            status = str(status_payload.get("status", "")).lower()
            if status == "dubbed":
                break
            if status in {"failed", "error"}:
                raise HTTPException(status_code=502, detail=f"ElevenLabs dubbing failed: {status_payload}")
            time.sleep(ELEVEN_DUBBING_POLL_INTERVAL_SECONDS)
        else:
            raise HTTPException(status_code=504, detail="Timed out waiting for ElevenLabs dubbing completion.")

        dubbed_bytes = eleven_download_dubbed_file(dubbing_id, language_used)
        final_video_url = None

        if source_media.endswith(".mp4"):
            final_mp4 = os.path.join(session_dir, "final.mp4")
            with open(final_mp4, "wb") as f:
                f.write(dubbed_bytes)
            final_video_url = f"/api/video/{session_id}"

            output_wav = os.path.join(session_dir, "final.wav")
            cmd = ["ffmpeg", "-y", "-i", final_mp4, "-vn", "-acodec", "pcm_s16le", output_wav]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Failed to extract WAV from dubbed video: {result.stderr}")
        else:
            output_mp3 = os.path.join(session_dir, "dubbed.mp3")
            with open(output_mp3, "wb") as f:
                f.write(dubbed_bytes)
            output_wav = os.path.join(session_dir, "final.wav")
            cmd = ["ffmpeg", "-y", "-i", output_mp3, "-acodec", "pcm_s16le", output_wav]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Failed to convert dubbed MP3 to WAV: {result.stderr}")

        return JSONResponse(content={
            "audio_url": f"/api/audio/{session_id}",
            "video_url": final_video_url,
            "dubbing_id": dubbing_id,
            "provider": "elevenlabs",
            "language_used": language_used,
            "warning": warning_message
        })
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))
        

# -----------------------------------------
# Direct ElevenLabs End-to-End Dub Endpoints
# -----------------------------------------

ELEVEN_DIRECT_DUB_LANGUAGES = [
    {"code": "hi",  "name": "Hindi",       "flag": "🇮🇳"},
    {"code": "en",  "name": "English",     "flag": "🇬🇧"},
    {"code": "es",  "name": "Spanish",     "flag": "🇪🇸"},
    {"code": "fr",  "name": "French",      "flag": "🇫🇷"},
    {"code": "de",  "name": "German",      "flag": "🇩🇪"},
    {"code": "ja",  "name": "Japanese",    "flag": "🇯🇵"},
    {"code": "zh",  "name": "Chinese",     "flag": "🇨🇳"},
    {"code": "ar",  "name": "Arabic",      "flag": "🇸🇦"},
    {"code": "pt",  "name": "Portuguese",  "flag": "🇧🇷"},
    {"code": "it",  "name": "Italian",     "flag": "🇮🇹"},
    {"code": "ko",  "name": "Korean",      "flag": "🇰🇷"},
    {"code": "nl",  "name": "Dutch",       "flag": "🇳🇱"},
    {"code": "pl",  "name": "Polish",      "flag": "🇵🇱"},
    {"code": "ru",  "name": "Russian",     "flag": "🇷🇺"},
    {"code": "tr",  "name": "Turkish",     "flag": "🇹🇷"},
    {"code": "sv",  "name": "Swedish",     "flag": "🇸🇪"},
    {"code": "ta",  "name": "Tamil",       "flag": "🇮🇳"},
    {"code": "te",  "name": "Telugu",      "flag": "🇮🇳"},
    {"code": "id",  "name": "Indonesian",  "flag": "🇮🇩"},
    {"code": "ms",  "name": "Malay",       "flag": "🇲🇾"},
    {"code": "uk",  "name": "Ukrainian",   "flag": "🇺🇦"},
    {"code": "el",  "name": "Greek",       "flag": "🇬🇷"},
    {"code": "vi",  "name": "Vietnamese",  "flag": "🇻🇳"},
    {"code": "fil", "name": "Filipino",    "flag": "🇵🇭"},
    {"code": "ro",  "name": "Romanian",    "flag": "🇷🇴"},
    {"code": "hu",  "name": "Hungarian",   "flag": "🇭🇺"},
    {"code": "cs",  "name": "Czech",       "flag": "🇨🇿"},
    {"code": "da",  "name": "Danish",      "flag": "🇩🇰"},
    {"code": "fi",  "name": "Finnish",     "flag": "🇫🇮"},
    {"code": "no",  "name": "Norwegian",   "flag": "🇳🇴"},
    {"code": "sk",  "name": "Slovak",      "flag": "🇸🇰"},
    {"code": "bg",  "name": "Bulgarian",   "flag": "🇧🇬"},
]


class DirectDubFinalizeRequest(BaseModel):
    session_id: str
    dubbing_id: str
    target_lang: str


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
    """
    Start an end-to-end ElevenLabs dubbing job.
    Saves the uploaded file to a new session directory, submits to ElevenLabs,
    and returns session_id + dubbing_id immediately for the client to poll.
    """
    cleanup_expired_sessions()

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    original_filename = file.filename or "input.mp4"
    ext = os.path.splitext(original_filename)[1].lower() or ".mp4"
    file_path = os.path.join(session_dir, f"input{ext}")

    # Stream uploaded file to disk
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

    # Submit dubbing job to ElevenLabs (blocking I/O → thread)
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

    # Persist metadata so /finalize can look it up
    meta = {
        "dubbing_id": dubbing_id,
        "target_lang": target_lang,
        "source_lang": source_lang,
        "file_path": file_path,
        "is_video": ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"),
    }
    with open(os.path.join(session_dir, "meta.json"), "w") as mf:
        json.dump(meta, mf)

    print(f"[DirectDub] Started dubbing_id={dubbing_id} session={session_id} lang={target_lang}")
    return JSONResponse(content={
        "session_id": session_id,
        "dubbing_id": dubbing_id,
        "status": "pending",
    })


@app.get("/api/dub-direct/status/{dubbing_id}")
async def dub_direct_status(dubbing_id: str):
    """Proxy the ElevenLabs dubbing status so the browser can poll it."""
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
    """Download the dubbed content and save it to the session directory."""
    session_dir = os.path.join(TEMP_DIR, session_id)
    if not os.path.isdir(session_dir):
        raise HTTPException(status_code=404, detail="Session not found.")

    meta_path = os.path.join(session_dir, "meta.json")
    is_video = False
    if os.path.exists(meta_path):
        with open(meta_path, "r") as mf:
            meta = json.load(mf)
        is_video = meta.get("is_video", False)

    # Download dubbed output from ElevenLabs
    dubbed_bytes = eleven_download_dubbed_file(dubbing_id, target_lang)
    audio_url = None
    video_url = None

    if is_video:
        final_mp4 = os.path.join(session_dir, "final.mp4")
        with open(final_mp4, "wb") as f:
            f.write(dubbed_bytes)
        video_url = f"/api/video/{session_id}"
        # Extract WAV for audio player
        output_wav = os.path.join(session_dir, "final.wav")
        cmd = ["ffmpeg", "-y", "-i", final_mp4, "-vn", "-acodec", "pcm_s16le", output_wav]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            audio_url = f"/api/audio/{session_id}"
        else:
            print(f"[DirectDub] WAV extraction warning: {result.stderr[:300]}")
    else:
        output_mp3 = os.path.join(session_dir, "dubbed.mp3")
        with open(output_mp3, "wb") as f:
            f.write(dubbed_bytes)
        output_wav = os.path.join(session_dir, "final.wav")
        cmd = ["ffmpeg", "-y", "-i", output_mp3, "-acodec", "pcm_s16le", output_wav]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            audio_url = f"/api/audio/{session_id}"

    print(f"[DirectDub] Finalized session={session_id} audio={audio_url} video={video_url}")
    return {"audio_url": audio_url, "video_url": video_url, "dubbing_id": dubbing_id}


@app.post("/api/dub-direct/finalize")
async def dub_direct_finalize(req: DirectDubFinalizeRequest):
    """Download the ElevenLabs dubbed result and return playback/download URLs."""
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
