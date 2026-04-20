import os
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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
                start_time = float(timestamps[0]) * 1000 if isinstance(timestamps, list) and timestamps else 0

                clip_path = os.path.join(session_dir, f"exp_clip_{index}.wav")
                try:
                    print(f"[Experiment] Synthesizing block {index}: {text[:40]}...")
                    audio_seg = generate_voice_clip_sarvam(text, sarvam_speaker, req.target_lang, clip_path)
                    mixed_audio = mixed_audio.overlay(audio_seg, position=start_time)
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
