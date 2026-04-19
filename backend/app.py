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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from sarvamai import SarvamAI
from pydub import AudioSegment
from dotenv import load_dotenv
import librosa
import numpy as np
import soundfile as sf

# Load env variables
load_dotenv()

app = FastAPI()

# allow_credentials=True is incompatible with allow_origins=["*"] (browser blocks; ACAO may be omitted).
# Vite/axios default is no cookies to the API — credentials False + wildcard is fine for local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Clients
SARVAM_KEY = os.getenv("SARVAM_API_KEY")
# ELEVEN_KEY = os.getenv("ELEVEN_API_KEY")  # disabled on `gemini` branch — synthesis uses Gemini TTS only
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
# ElevenLabs (commented out on `gemini` branch — see Gemini TTS below)
# ELEVEN_BASE_URL = os.getenv("ELEVEN_BASE_URL", "https://api.elevenlabs.io")
# ELEVEN_DUBBING_POLL_INTERVAL_SECONDS = float(os.getenv("ELEVEN_DUBBING_POLL_INTERVAL_SECONDS", "4"))
# ELEVEN_DUBBING_TIMEOUT_SECONDS = int(os.getenv("ELEVEN_DUBBING_TIMEOUT_SECONDS", "600"))
ELEVEN_SUPPORTED_TARGET_LANGS = os.getenv(
    "ELEVEN_SUPPORTED_TARGET_LANGS",
    "hi-IN,bn-IN,ta-IN,te-IN,kn-IN,ml-IN,gu-IN,pa-IN,od-IN,en-IN",
)
SARVAM_FALLBACK_SPEAKER = os.getenv("SARVAM_FALLBACK_SPEAKER", "priya")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MAX_WORKERS = int(os.getenv("GEMINI_MAX_WORKERS", "1"))
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "5"))
GEMINI_RETRY_BASE_SECONDS = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "2.0"))
GEMINI_RETRY_MAX_SECONDS = float(os.getenv("GEMINI_RETRY_MAX_SECONDS", "45.0"))
GEMINI_MIN_LENGTH_RATIO = float(os.getenv("GEMINI_MIN_LENGTH_RATIO", "0.75"))
# Minimum playback window (seconds) granted to any TTS clip regardless of STT segment length.
# Prevents short diarization windows from cutting TTS mid-sentence (e.g. last speaker turn of 0.3s).
GEMINI_TTS_MIN_CLIP_DURATION_S = float(os.getenv("GEMINI_TTS_MIN_CLIP_DURATION_S", "1.8"))
# For blocks with NO subsequent different-speaker (e.g. last block), allow the TTS audio to run
# for at least this many extra seconds beyond the STT segment end before trimming.
GEMINI_TTS_LAST_BLOCK_TAIL_S = float(os.getenv("GEMINI_TTS_LAST_BLOCK_TAIL_S", "5.0"))
# Gemini native TTS (https://ai.google.dev/gemini-api/docs/speech-generation)
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_SAMPLE_RATE = int(os.getenv("GEMINI_TTS_SAMPLE_RATE", "24000"))
GEMINI_TTS_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TTS_TIMEOUT_SECONDS", "120"))
GEMINI_TTS_RETRY_ATTEMPTS = int(os.getenv("GEMINI_TTS_RETRY_ATTEMPTS", "4"))
# Time-stretch to fit diarization windows: librosa phase-vocoder often sounds robotic on speech.
# quality (default): micro-stretch only when |actual/target - 1| <= GEMINI_TTS_STRETCH_MAX_DEVIATION (see below).
# full: always stretch within legacy 0.7–1.4× bounds. off: never stretch (most natural; may overlap/trim).
GEMINI_TTS_TIME_STRETCH = os.getenv("GEMINI_TTS_TIME_STRETCH", "quality").strip().lower()
if GEMINI_TTS_TIME_STRETCH not in ("off", "quality", "full"):
    GEMINI_TTS_TIME_STRETCH = "quality"
# quality mode: allow librosa only when duration is within this fraction of the slot (e.g. 0.05 = ±5%).
GEMINI_TTS_STRETCH_MAX_DEVIATION = float(os.getenv("GEMINI_TTS_STRETCH_MAX_DEVIATION", "0.05"))
GEMINI_TTS_TEMPERATURE = float(os.getenv("GEMINI_TTS_TEMPERATURE", "1.0"))
# Fade last N ms when forcing a trim so word tails are not chopped with a hard digital edge.
GEMINI_TTS_TRIM_FADE_MS = int(os.getenv("GEMINI_TTS_TRIM_FADE_MS", "55"))
# Max ms of bleed past the STT slot boundary before a hard trim fires.
# Audio is allowed to run into the silence gap between speakers up to this limit
# so sentences finish naturally without chopped tails.
# Set to 0 to restore the old hard-trim-at-slot behaviour.
GEMINI_TTS_BLEED_MAX_MS = int(os.getenv("GEMINI_TTS_BLEED_MAX_MS", "3000"))
# If set (default -2.5), scale final dub mix down when peaks are louder than this dBFS (reduces clip harshness).
_fp = os.getenv("GEMINI_TTS_FINAL_TARGET_PEAK_DB", "-2.5").strip().lower()
GEMINI_TTS_FINAL_TARGET_PEAK_DB: Optional[float]
if _fp in ("", "off", "false", "none"):
    GEMINI_TTS_FINAL_TARGET_PEAK_DB = None
else:
    GEMINI_TTS_FINAL_TARGET_PEAK_DB = float(_fp)
# Added to each dub clip overlay position (ms). Negative pulls audio earlier on the timeline — STT
# segment starts often trail lip movement slightly, which reads as "dub is late". Tune per language/source.
GEMINI_DUB_OVERLAY_NUDGE_MS = float(os.getenv("GEMINI_DUB_OVERLAY_NUDGE_MS", "-80"))

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


class PreviewVoiceRequest(BaseModel):
    voice_id: str
    target_lang: str = "hi-IN"


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


# ---------------------------------------------------------------------------
# ElevenLabs TTS / dubbing — commented out on `gemini` branch (use Gemini TTS).
# ---------------------------------------------------------------------------
# def eleven_headers() -> Dict[str, str]:
#     if not ELEVEN_KEY:
#         raise HTTPException(status_code=500, detail="ELEVEN_API_KEY is not configured.")
#     return {"xi-api-key": ELEVEN_KEY}
#
# def eleven_post_dubbing(...): ...
# def eleven_get_dubbing(...): ...
# def eleven_download_dubbed_file(...): ...


VOICE_CHARACTER_TRAITS = {
    "Kore": "Firm and warm", "Aoede": "Breezy and clear", "Zephyr": "Bright and airy", 
    "Leda": "Professional and steady", "Autonoe": "Rich and melodic", "Despina": "Confident and sharp", 
    "Vindemiatrix": "Steady and reliable",
    "Puck": "Upbeat and youthful", "Charon": "Informative and deep", "Fenrir": "Excitable and intense", 
    "Orus": "Energetic and bold", "Iapetus": "Mature and weighted", "Achird": "Warm and friendly", 
    "Algenib": "Narrative and smooth", "Schedar": "Crisp and clear", "Enceladus": "Steady and narrative"
}

def stage3_voice_id_to_gemini_prebuilt(voice_id: str) -> str:
    """Map Stage 3 Sarvam-style voice ids or direct Gemini voice names to Gemini prebuilt TTS voice names."""
    vid = (voice_id or "auto").strip()
    
    female_voices = ["Kore", "Aoede", "Zephyr", "Leda", "Autonoe", "Despina", "Vindemiatrix"]
    male_voices = ["Puck", "Charon", "Fenrir", "Orus", "Iapetus", "Achird", "Algenib", "Schedar", "Enceladus"]
    
    # If it's already a valid Gemini voice name (case-sensitive as per API), return it
    if vid in female_voices or vid in male_voices:
        return vid
        
    vid_lower = vid.lower()
    if vid_lower == "auto":
        return "Kore"

    female = {
        "ritu", "priya", "neha", "pooja", "simran", "kavya", "ishita", "shreya", "roopa", "tanya",
        "shruti", "suhani", "kavitha", "rupali", "niharika", "amelia", "sophia", "mani",
    }
    male = {
        "aditya", "ashutosh", "rahul", "rohan", "amit", "dev", "ratan", "varun", "manan", "sumit",
        "kabir", "aayan", "shubh", "advait", "anand", "tarun", "sunny", "gokul", "vijay", "mohit",
        "rehan", "soham",
    }
    if vid in female:
        return female_voices[abs(hash(vid)) % len(female_voices)]
    if vid in male:
        return male_voices[abs(hash(vid)) % len(male_voices)]
    return "Kore"


def _extract_gemini_tts_pcm_from_response(data: Dict[str, Any]) -> bytes:
    cands = data.get("candidates") or []
    if not cands:
        raise ValueError("Gemini TTS: empty candidates")
    parts = (((cands[0] or {}).get("content") or {}).get("parts") or [])
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if not inline:
            continue
        b64 = inline.get("data")
        if b64:
            return base64.b64decode(b64)
    raise ValueError("Gemini TTS: no inline audio part")


def _gemini_tts_instruction_text(
    line: str,
    trait: str,
    target_lang_hint: str,
    emotion: Optional[str],
) -> str:
    """Keep instructions short; long 'actor coach' prompts tend to sound performative or announcer-like."""
    em = (emotion or "").strip().lower()
    if em in ("", "neutral"):
        tone = (
            "Conversational and relaxed, like someone talking to a friend - "
            "not a narrator or robot."
        )
    else:
        tone = (
            f"Let the tone reflect [{em}] subtly - still sound like a real person, "
            "not a stage performance."
        )
    lang = (target_lang_hint or "").strip() or "the target language"
    return "\n".join(
        [
            f"Speak in {lang} using a natural speaking rate and intonation.",
            f"Voice vibe: {trait}. {tone}",
            "",
            "Say only the following line (no preamble, no quotes):",
            line.strip(),
        ]
    )


def generate_gemini_tts_audiosegment(
    text: str,
    gemini_voice_name: str,
    target_lang_hint: str,
    emotion: Optional[str] = None,
) -> AudioSegment:
    """Single-speaker line via Gemini native TTS (PCM s16le mono @ GEMINI_TTS_SAMPLE_RATE)."""
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured for TTS.")
    line = (text or "").strip()
    if not line:
        raise ValueError("empty text")
    trait = VOICE_CHARACTER_TRAITS.get(gemini_voice_name, "Natural and conversational")
    prompt = _gemini_tts_instruction_text(line, trait, target_lang_hint, emotion)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "temperature": GEMINI_TTS_TEMPERATURE,
            "topP": 0.95,
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": gemini_voice_name,
                    }
                }
            },
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_TTS_MODEL}:generateContent?key={urllib.parse.quote(GEMINI_API_KEY)}"
    )
    last_err: Optional[Exception] = None
    for attempt in range(GEMINI_TTS_RETRY_ATTEMPTS):
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TTS_TIMEOUT_SECONDS) as response:
                raw = json.loads(response.read().decode("utf-8"))
            pcm = _extract_gemini_tts_pcm_from_response(raw)
            buf = io.BytesIO(pcm)
            try:
                return AudioSegment.from_wav(buf)
            except Exception:
                buf.seek(0)
                return AudioSegment.from_raw(
                    buf,
                    sample_width=2,
                    frame_rate=GEMINI_TTS_SAMPLE_RATE,
                    channels=1,
                    format="raw",
                )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_err = exc
            if exc.code in (429, 500, 502, 503) and attempt < GEMINI_TTS_RETRY_ATTEMPTS - 1:
                time.sleep(min(8.0, GEMINI_RETRY_BASE_SECONDS * (2 ** attempt)))
                continue
            if exc.code == 403 and "unregistered callers" in detail.lower():
                raise HTTPException(
                    status_code=403,
                    detail="Gemini API key invalid for TTS. Set GEMINI_API_KEY and restart.",
                ) from exc
            raise HTTPException(status_code=exc.code, detail=f"Gemini TTS failed: {detail}") from exc
        except Exception as exc:
            last_err = exc
            if attempt < GEMINI_TTS_RETRY_ATTEMPTS - 1:
                time.sleep(min(8.0, GEMINI_RETRY_BASE_SECONDS * (2 ** attempt)))
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("Gemini TTS: unexpected")


def _is_gemini_quota_exhausted(exc: Exception) -> bool:
    """Return True when the exception signals a daily/per-model quota exhaustion
    (RESOURCE_EXHAUSTED / 429 with GenerateRequestsPerDay violation)."""
    txt = str(exc).lower()
    return (
        "resource_exhausted" in txt
        or "generate_requests_per_model_per_day" in txt
        or ("429" in txt and "quota" in txt)
    )


# Map Gemini voice names to reasonable Sarvam speaker ids (best-effort gender match).
_GEMINI_TO_SARVAM_SPEAKER: Dict[str, str] = {
    # Female Gemini voices → female Sarvam speakers
    "Kore": "priya", "Aoede": "ritu", "Zephyr": "neha",
    "Leda": "kavya", "Autonoe": "roopa", "Despina": "suhani",
    "Vindemiatrix": "shreya",
    # Male Gemini voices → male Sarvam speakers
    "Puck": "aditya", "Charon": "rahul", "Fenrir": "rohan",
    "Orus": "amit", "Iapetus": "kabir", "Achird": "anand",
    "Algenib": "tarun", "Schedar": "varun", "Enceladus": "sumit",
}


def _gemini_voice_to_sarvam_speaker(gemini_voice: str) -> str:
    """Return a Sarvam speaker id that best matches the Gemini voice gender."""
    return _GEMINI_TO_SARVAM_SPEAKER.get(gemini_voice, SARVAM_FALLBACK_SPEAKER)


def generate_tts_with_sarvam_fallback(
    text: str,
    gemini_voice: str,
    target_lang: str,
    emotion: Optional[str],
    *,
    gemini_quota_exhausted: bool = False,
) -> Tuple[AudioSegment, str, bool]:
    """Attempt Gemini TTS; on quota exhaustion fall back to Sarvam TTS.

    Returns:
        (audio_segment, provider_used, quota_exhausted_flag)
        provider_used is 'gemini' or 'sarvam_fallback'.
        quota_exhausted_flag is True if Gemini quota was hit (caller should
        set a session flag to skip Gemini for remaining blocks).
    """
    if not gemini_quota_exhausted:
        try:
            seg = generate_gemini_tts_audiosegment(
                text=text,
                gemini_voice_name=gemini_voice,
                target_lang_hint=target_lang,
                emotion=emotion,
            )
            return seg, "gemini", False
        except Exception as exc:
            if _is_gemini_quota_exhausted(exc):
                print(
                    f"[TTS Fallback] Gemini quota exhausted — switching to Sarvam TTS "
                    f"for remaining blocks. (Error: {exc})"
                )
                # Fall through to Sarvam below, setting the flag.
            else:
                raise  # Non-quota error: re-raise so caller sees it

    # Sarvam fallback path
    sarvam_speaker = _gemini_voice_to_sarvam_speaker(gemini_voice)
    print(f"[Sarvam Fallback TTS] speaker={sarvam_speaker} lang={target_lang}: {text[:50]}...")
    seg = sarvam_client.text_to_speech.convert(
        text=text,
        target_language_code=normalize_sarvam_lang_code(target_lang),
        speaker=sarvam_speaker,
        model="bulbul:v3",
        speech_sample_rate=24000,
        output_audio_codec="wav",
    )
    if not getattr(seg, "audios", None):
        raise ValueError("Sarvam TTS fallback returned empty audio")
    audio_seg = AudioSegment.from_wav(io.BytesIO(base64.b64decode(seg.audios[0])))
    return audio_seg, "sarvam_fallback", True


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


def get_available_duration(blocks: List[Dict[str, Any]], index: int) -> float:
    """Upper bound on how long a dub clip should play for this block.

    Uses the later of:
      (a) STT segment length,
      (b) time until the next *different* speaker,
      (c) GEMINI_TTS_MIN_CLIP_DURATION_S — hard floor so short diarization windows
          never chop TTS mid-sentence.

    For the last block (or when no next different speaker is found within the remaining
    blocks), an extra tail of GEMINI_TTS_LAST_BLOCK_TAIL_S is added beyond the STT
    segment end, preventing the final speaker turn from being cut short.
    """
    current = blocks[index]
    ts = current.get("timestamps") or [0.0, 0.0]
    start = float(ts[0])
    end = float(ts[1]) if len(ts) > 1 else start + 1.0
    if end <= start:
        end = start + 0.35
    stt_span = max(0.35, end - start)

    found_diff_speaker = False
    until_diff = stt_span
    for j in range(index + 1, len(blocks)):
        next_block = blocks[j]
        current_speaker = current.get("speakers", ["S0"])[0]
        next_speaker = next_block.get("speakers", ["S0"])[0]
        if next_speaker != current_speaker:
            until_diff = max(0.35, float(next_block["timestamps"][0]) - start)
            found_diff_speaker = True
            break

    # No next different speaker found → last (or only) block for this speaker.
    # Grant a generous tail so the full TTS sentence isn't cut off.
    if not found_diff_speaker:
        until_diff = stt_span + GEMINI_TTS_LAST_BLOCK_TAIL_S

    raw = max(stt_span, until_diff)
    # Always grant at least MIN_CLIP_DURATION seconds regardless of STT window size.
    return max(raw, GEMINI_TTS_MIN_CLIP_DURATION_S)


def get_hard_limit_duration(blocks: List[Dict[str, Any]], index: int) -> float:
    """Absolute hard limit (seconds) = start of the very next block, regardless of speaker.

    This is the point at which the next speaker's voice begins.  TTS audio must
    not play past this point or it will clash with the following line.
    Returns a large sentinel (9999) for the last block.
    """
    current = blocks[index]
    ts = current.get("timestamps") or [0.0, 0.0]
    start = float(ts[0])

    for j in range(index + 1, len(blocks)):
        nb = blocks[j]
        nb_ts = nb.get("timestamps") or []
        if nb_ts:
            return max(0.0, float(nb_ts[0]) - start)
    return 9999.0  # last block — no following speech


def stretch_audio_to_fit(audio_segment: AudioSegment, target_duration_ms: float) -> AudioSegment:
    """Time-stretch audio using librosa to fit a target duration without changing pitch."""
    current_duration_ms = len(audio_segment)
    if current_duration_ms <= 0 or target_duration_ms <= 0:
        return audio_segment

    if GEMINI_TTS_TIME_STRETCH == "off":
        return audio_segment

    if abs(current_duration_ms - target_duration_ms) < 100:
        return audio_segment  # close enough, skip

    ratio = current_duration_ms / target_duration_ms
    if GEMINI_TTS_TIME_STRETCH == "quality":
        # Late in a timeline, slots get tight: the old ±12% band still invoked librosa a lot.
        # Only micro-stretch when already within a small fraction of the target length.
        dev = max(0.0, min(0.25, GEMINI_TTS_STRETCH_MAX_DEVIATION))
        if abs(ratio - 1.0) > dev:
            return audio_segment

    # Convert pydub AudioSegment to numpy array for librosa
    # Note: AudioSegment objects are usually 16-bit PCM
    samples = np.array(audio_segment.get_array_of_samples()).astype(np.float32)
    # Normalize if it's 16-bit
    if audio_segment.sample_width == 2:
        samples /= 32768.0
    
    rate = current_duration_ms / target_duration_ms
    # Clamp stretch: don't go below 0.7x or above 1.4x (quality degrades)
    # librosa.effects.time_stretch 'rate' parameter: > 1.0 is faster (shorter), < 1.0 is slower (longer)
    # If target is shorter, rate > 1.0. 
    rate = max(0.7, min(1.4, rate))
    
    try:
        stretched = librosa.effects.time_stretch(samples, rate=rate)
        # Convert back to 16-bit PCM
        stretched = (stretched * 32767.0).astype(np.int16)
        
        return AudioSegment(
            stretched.tobytes(),
            frame_rate=audio_segment.frame_rate,
            sample_width=audio_segment.sample_width,
            channels=audio_segment.channels
        )
    except Exception as e:
        print(f"Time-stretch failed: {e}")
        return audio_segment


def trim_audio_to_max_duration_ms(
    seg: AudioSegment,
    max_ms: float,
    trim_slack_ms: float = 50.0,
    tail_fade_ms: int = 0,
) -> AudioSegment:
    """Shorten audio that exceeds a timeline slot; optional tail fade avoids a hard digital cut."""
    lim = max(0, int(max_ms))
    if len(seg) <= lim + int(trim_slack_ms):
        return seg
    out = seg[:lim]
    fade = int(tail_fade_ms)
    if fade > 0 and len(out) > fade * 2:
        out = out.fade_out(min(fade, max(1, len(out) // 4)))
    return out


def limit_mix_peak_headroom(seg: AudioSegment, target_peak_db: float) -> AudioSegment:
    """Scale mix down when peaks exceed target dBFS (stacked overlays can clip int16)."""
    if len(seg) == 0:
        return seg
    try:
        peak = seg.max_dBFS
    except Exception:
        return seg
    if peak == float("-inf") or peak <= target_peak_db:
        return seg
    return seg.apply_gain(target_peak_db - peak)


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
You are an expert dialogue coach analyzing dubbed video.
Task:
Refine the current translated line so it sounds natural when spoken, but stay very close to the original meaning. 
GROUNDING: Use the provided audio chunk to analyze the speaker's original tone, pace, and emotion.
EMOTION TAGS: Add exactly ONE appropriate emotion tag in square brackets (e.g., [laughs], [whispers], [excitedly], [sarcastically]) at the very start of the line to guide TTS delivery. Use lowercase adverb/adjective style tags.
Do NOT use multiple tags unless there is a huge tonal shift.
Do NOT use vague tags like [normal].

Hard constraints:
- Output language: {target_lang}
- Audio tags MUST be in English in square brackets at the start.
- Tone: Match the speaker ({speaker_label}, {speaker_gender}) from the audio chunk.
- Return EXACTLY one line: [tag] Translated text.

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
            
            refined = None
            emotion_tags = []
            cleaned_refined = ""
            
            # Validation-aware retry loop (3 attempts)
            for attempt in range(3):
                refined = call_gemini_with_retry(
                    text=text,
                    audio_chunk_b64=chunk_b64,
                    speaker_label=speaker,
                    speaker_gender=gender,
                    source_lang=resolved_source_lang,
                    target_lang=resolved_target_lang,
                )
                emotion_tags, cleaned_refined = extract_emotion_tags_and_clean_text(refined)
                
                # Success condition: exactly one emotion tag found
                if len(emotion_tags) == 1:
                    break
                print(f"[Gemini Validation] Attempt {attempt+1} failed: found {len(emotion_tags)} tags. Retrying...")

            updated = {
                **block,
                "transcript": cleaned_refined or text,
                "emotion_tags": emotion_tags if len(emotion_tags) > 0 else ["neutral"],
            }
            return index, updated, None
        except Exception as exc:
            if "emotion_tags" not in block:
                block["emotion_tags"] = ["neutral"]
            return index, block, str(exc)

    def _neutral_passthrough(index: int, block: Dict[str, Any]) -> tuple:
        """Return block unchanged with neutral tag (quota fast-skip path)."""
        out = dict(block)
        if "emotion_tags" not in out:
            out["emotion_tags"] = ["neutral"]
        return index, out, None

    # Sequential — not a thread pool — so we can bail the moment Gemini quota is
    # exhausted.  A ThreadPoolExecutor keeps all futures retrying with 45s waits
    # even after quota hits on the very first block, costing many minutes for nothing.
    gemini_enhance_quota_hit = False
    gemini_enhance_skipped = 0

    def _run_enhance_sequential():
        nonlocal gemini_enhance_quota_hit, gemini_enhance_skipped
        for i, block in enumerate(req.transcript_blocks):
            if gemini_enhance_quota_hit:
                gemini_enhance_skipped += 1
                idx, out_block, err = _neutral_passthrough(i, block)
            else:
                idx, out_block, err = process_block(i, block)
                # Detect quota exhaustion — skip all remaining blocks immediately.
                if err and _is_gemini_quota_exhausted(Exception(err)):
                    gemini_enhance_quota_hit = True
                    print(
                        f"[Enhance] Gemini quota hit at block {i} — "
                        f"remaining blocks will use neutral emotion tags (no retry wait)."
                    )
            enhanced_blocks[idx] = out_block
            if err:
                failed_blocks.append({"index": idx, "error": err})

    await asyncio.to_thread(_run_enhance_sequential)

    enhance_processing_seconds = time.perf_counter() - t_enhance0

    return JSONResponse(content={
        "blocks": enhanced_blocks,
        "failed_block_count": len(failed_blocks),
        "failed_blocks": failed_blocks,
        "estimated_enhance_seconds": round(estimated_enhance_seconds, 1),
        "enhance_processing_seconds": round(enhance_processing_seconds, 2),
        "gemini_enhance_quota_exhausted": gemini_enhance_quota_hit,
        "gemini_enhance_skipped_blocks": gemini_enhance_skipped,
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
        # `gemini` branch: all synthesis uses Gemini native TTS (speech-generation), timeline-mixed like the old
        # Sarvam experiment path. ElevenLabs file dubbing is disabled (see commented helpers above).
        if req.target_duration_ms > 0:
            target_ms = int(req.target_duration_ms)
        else:
            target_ms = 0
            for block in req.transcript_blocks:
                timestamps = block.get("timestamps", [])
                if timestamps and len(timestamps) > 1:
                    end_time_ms = float(timestamps[1]) * 1000
                    if end_time_ms > target_ms:
                        target_ms = end_time_ms
            target_ms = int(target_ms) + 5000

        mixed_audio = AudioSegment.silent(duration=target_ms)
        voice_map_dict = {m.speaker_id: m.voice_id for m in req.voice_map}
        failed_blocks = []
        gemini_quota_hit = False   # session-level flag: skip Gemini once quota is exhausted
        sarvam_fallback_count = 0

        for index, block in enumerate(req.transcript_blocks):
            text = (block.get("transcript") or "").strip()
            if not text:
                continue

            speakers = block.get("speakers", [])
            timestamps = block.get("timestamps", [])
            primary_speaker = speakers[0] if speakers else "S0"
            voice_raw = voice_map_dict.get(primary_speaker, "auto")
            gemini_voice = stage3_voice_id_to_gemini_prebuilt(voice_raw)
            start_time_raw = float(timestamps[0]) * 1000 if isinstance(timestamps, list) and timestamps else 0.0
            start_time = max(0.0, start_time_raw + GEMINI_DUB_OVERLAY_NUDGE_MS)

            try:
                # Extract first emotion tag for refined performance
                tag = block.get("emotion_tags", [])[0] if block.get("emotion_tags") else None
                provider_label = "Sarvam(fallback)" if gemini_quota_hit else "Gemini"
                print(f"[{provider_label} TTS] block {index} voice={gemini_voice} emotion={tag}: {text[:40]}...")

                audio_seg, provider_used, quota_hit = generate_tts_with_sarvam_fallback(
                    text=text,
                    gemini_voice=gemini_voice,
                    target_lang=req.target_lang,
                    emotion=tag,
                    gemini_quota_exhausted=gemini_quota_hit,
                )
                if quota_hit:
                    gemini_quota_hit = True
                    sarvam_fallback_count += 1

                # ── Duration management ────────────────────────────────────────
                # soft_ms  = preferred slot (until next *different* speaker or min floor)
                # hard_ms  = absolute ceiling (until the very next block starts)
                # We allow the audio to bleed past soft_ms into silence up to
                # min(hard_ms, soft_ms + BLEED_MAX_MS) so sentences finish naturally.
                available_s = get_available_duration(req.transcript_blocks, index)
                soft_ms = available_s * 1000
                hard_s  = get_hard_limit_duration(req.transcript_blocks, index)
                hard_ms = hard_s * 1000

                # Ceiling the bleed: don't go past the next speaker's first word.
                bleed_ceiling_ms = min(hard_ms, soft_ms + GEMINI_TTS_BLEED_MAX_MS)

                fitted_audio = stretch_audio_to_fit(audio_seg, soft_ms)

                tts_len = len(fitted_audio)
                if tts_len <= bleed_ceiling_ms + 50:
                    # Audio fits within the bleed ceiling — play it in full.
                    if tts_len > soft_ms + 50:
                        print(
                            f"[{provider_used} TTS] block {index}: "
                            f"TTS {tts_len}ms bleeds {int(tts_len - soft_ms)}ms into gap "
                            f"(hard limit {int(hard_ms)}ms) — tail preserved"
                        )
                else:
                    # Audio would crash into the next speaker — hard trim with fade.
                    pre_len = tts_len
                    fitted_audio = trim_audio_to_max_duration_ms(
                        fitted_audio,
                        bleed_ceiling_ms,
                        trim_slack_ms=50.0,
                        tail_fade_ms=GEMINI_TTS_TRIM_FADE_MS,
                    )
                    print(
                        f"[{provider_used} TTS] block {index}: "
                        f"TTS {pre_len}ms trimmed to {int(bleed_ceiling_ms)}ms "
                        f"(next speaker at {int(hard_ms)}ms) — tail clipped"
                    )

                mixed_audio = mixed_audio.overlay(fitted_audio, position=start_time)
            except Exception as exc:
                print(f"[TTS] Synthesis failed for block {index}: {exc}")
                failed_blocks.append({"index": index, "error": str(exc)})

        output_wav = os.path.join(session_dir, "final.wav")
        if GEMINI_TTS_FINAL_TARGET_PEAK_DB is not None:
            mixed_audio = limit_mix_peak_headroom(mixed_audio, GEMINI_TTS_FINAL_TARGET_PEAK_DB)
        mixed_audio.export(output_wav, format="wav")

        total_non_empty_blocks = sum(
            1 for block in req.transcript_blocks if (block.get("transcript") or "").strip()
        )
        if total_non_empty_blocks > 0 and len(failed_blocks) == total_non_empty_blocks:
            raise HTTPException(
                status_code=502,
                detail=(
                    "All synthesis blocks failed for Gemini TTS. "
                    "Check GEMINI_API_KEY, model availability (gemini-2.5-flash-preview-tts), and logs."
                ),
            )

        final_video_url = None
        input_video = os.path.join(session_dir, "input.mp4")
        if os.path.exists(input_video):
            final_mp4 = os.path.join(session_dir, "final.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", input_video, "-i", output_wav,
                "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                final_mp4,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                final_video_url = f"/api/video/{session_id}"
            else:
                print(f"[Gemini TTS] FFmpeg muxing error: {result.stderr}")

        provider_summary = "gemini_tts"
        if gemini_quota_hit and sarvam_fallback_count > 0:
            if sarvam_fallback_count == sum(1 for b in req.transcript_blocks if (b.get("transcript") or "").strip()):
                provider_summary = "sarvam_fallback"
            else:
                provider_summary = f"gemini_tts+sarvam_fallback({sarvam_fallback_count}blocks)"
        if gemini_quota_hit:
            print(
                f"[TTS] Gemini quota was exhausted during this run. "
                f"{sarvam_fallback_count} block(s) synthesised via Sarvam TTS fallback."
            )

        return JSONResponse(
            content={
                "audio_url": f"/api/audio/{session_id}",
                "video_url": final_video_url,
                "provider": provider_summary,
                "tts_model": GEMINI_TTS_MODEL,
                "synthesis_mode_used": req.synthesis_mode,
                "failed_block_count": len(failed_blocks),
                "failed_blocks": failed_blocks,
                "gemini_quota_exhausted": gemini_quota_hit,
                "sarvam_fallback_blocks": sarvam_fallback_count,
            }
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))
        

@app.post("/api/preview-voice")
async def preview_voice(req: PreviewVoiceRequest):
    """Generate a short audio sample for a specific Gemini voice."""
    try:
        gemini_voice = stage3_voice_id_to_gemini_prebuilt(req.voice_id)
        
        # Gender-aware standard preview text in Hindi
        is_male = gemini_voice in ["Puck", "Charon", "Fenrir", "Orus", "Iapetus", "Achird", "Algenib", "Schedar", "Enceladus"]
        suffix = "रहा हूँ" if is_male else "रही हूँ"
        preview_text = f"नमस्ते, मैं आपकी पसंद की इस आवाज़ का उपयोग करके बोल {suffix}।"
        
        if "en" in req.target_lang.lower():
            preview_text = "Hello, I am speaking using this voice profile you have selected."
        
        print(f"[Gemini TTS Preview] voice={gemini_voice} gender={'male' if is_male else 'female'} lang={req.target_lang}")
        
        audio_seg = generate_gemini_tts_audiosegment(
            text=preview_text, 
            gemini_voice_name=gemini_voice, 
            target_lang_hint=req.target_lang,
            emotion="friendly"
        )
        
        buffer = io.BytesIO()
        audio_seg.export(buffer, format="wav")
        buffer.seek(0)
        
        return StreamingResponse(
            buffer, 
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=preview_{gemini_voice}.wav"}
        )
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
