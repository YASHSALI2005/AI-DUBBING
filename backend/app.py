import os
import json
import uuid
import time
import subprocess
import shutil
import traceback
import base64
import io
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
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

# -----------------
# Pydantic Models
# -----------------
class TranslateRequest(BaseModel):
    transcript_blocks: List[Dict[str, Any]]
    target_lang: str
    source_lang: str = "en-IN"

class SynthesisVoiceMap(BaseModel):
    speaker_id: str
    voice_id: str
    
class SynthesisRequest(BaseModel):
    session_id: Optional[str] = None
    transcript_blocks: List[Dict[str, Any]]
    voice_map: List[SynthesisVoiceMap]
    target_duration_ms: float = 0
    target_lang: str = 'hi-IN'

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

def generate_voice_clip(text: str, speaker: str, target_lang: str, output_path: str):
    """Use Sarvam TTS (bulbul:v3, 48kHz premium) to synthesize a voice clip."""
    res = sarvam_client.text_to_speech.convert(
        text=text,
        target_language_code=target_lang,
        speaker=speaker,
        model="bulbul:v3",
        speech_sample_rate=48000,  # Premium quality: 48kHz
        output_audio_codec="wav"
    )
    # Sarvam returns a list of base64-encoded PCM audio strings in res.audios
    if not res.audios:
        raise ValueError("Sarvam TTS returned empty audio")
    
    raw_bytes = base64.b64decode(res.audios[0])
    audio_seg = AudioSegment.from_wav(io.BytesIO(raw_bytes))
    audio_seg.export(output_path, format="wav")
    return audio_seg


def generate_voice_clip_with_retry(text: str, speaker: str, target_lang: str, output_path: str):
    """
    Retry TTS when rate-limited (429) with exponential backoff.
    """
    for attempt in range(SYNTHESIS_RETRY_ATTEMPTS):
        try:
            return generate_voice_clip(text, speaker, target_lang, output_path)
        except Exception as exc:
            err = str(exc).lower()
            is_rate_limited = "429" in err or "rate limit" in err or "rate_limit_exceeded_error" in err
            last_attempt = attempt == SYNTHESIS_RETRY_ATTEMPTS - 1
            if not is_rate_limited or last_attempt:
                raise
            sleep_seconds = SYNTHESIS_RETRY_BASE_SECONDS * (2 ** attempt)
            print(
                f"Rate limited for speaker {speaker}. "
                f"Retry {attempt + 1}/{SYNTHESIS_RETRY_ATTEMPTS} in {sleep_seconds:.1f}s."
            )
            time.sleep(sleep_seconds)


def translate_text_with_retry(text: str, source_lang: str, target_lang: str) -> str:
    """Retry translate on transient server/rate-limit errors."""
    for attempt in range(TRANSLATE_RETRY_ATTEMPTS):
        try:
            res = sarvam_client.text.translate(
                input=text,
                source_language_code=source_lang,
                target_language_code=target_lang,
                model="sarvam-translate:v1"
            )
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

# -----------------
# API Endpoints
# -----------------

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
    
    with open(video_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
        
    # Extract Audio
    try:
        extract_audio(video_path, audio_path)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Audio extraction failed: {str(e)}")
        
    # Process STT Diarization via Sarvam
    try:
        # For batch job:
        job = sarvam_client.speech_to_text_job.create_job(
            model="saaras:v3",
            mode="transcribe",
            with_diarization=True,
            with_timestamps=True,
            language_code=language_code if language_code else None
        )
        job.upload_files(file_paths=[audio_path])
        job.start()
        job.wait_until_complete()
        
        output_folder = os.path.join(session_dir, "results")
        os.makedirs(output_folder, exist_ok=True)
        job.download_outputs(output_dir=output_folder)
        
        # Read the resulting json file which Sarvam names based on the input filename
        result_file = os.path.join(output_folder, "audio.mp3.json")
        with open(result_file, "r", encoding="utf-8") as rf:
            data = json.load(rf)
            
        return JSONResponse(content={
            "session_id": session_id,
            "data": data,
            "message": "Upload & Diarization successful"
        })
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"STT Pipeline failed: {str(e)}")


@app.post("/api/translate")
async def translate_text(req: TranslateRequest):
    translated_blocks = [None] * len(req.transcript_blocks)
    failed_blocks = []
    
    def process_block(index, block):
        speakers = block.get('speakers', [])
        timestamps = block.get('timestamps', [])
        original_text = block.get('transcript', '')
        
        # Translate the snippet using Sarvam AI
        try:
            if original_text.strip() and req.target_lang != req.source_lang:
                print(f"Translating block from {req.source_lang} to {req.target_lang}: {original_text[:50]}...")
                trans_text = translate_text_with_retry(original_text, req.source_lang, req.target_lang)
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
            
    return JSONResponse(content={
        "blocks": translated_blocks,
        "failed_block_count": len(failed_blocks),
        "failed_blocks": failed_blocks
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
        # Create a canvas matching the exact video length so the dubbed audio fits perfectly 1:1
        if req.target_duration_ms > 0:
            target_ms = int(req.target_duration_ms)
        else:
            # Fallback calculate the max timestamp
            target_ms = 0
            for block in req.transcript_blocks:
                timestamps = block.get('timestamps', [])
                if timestamps and len(timestamps) > 1:
                    end_time_ms = float(timestamps[1]) * 1000
                    if end_time_ms > target_ms:
                        target_ms = end_time_ms
            # Pad a little bit
            target_ms = int(target_ms) + 5000 
            
        mixed_audio = AudioSegment.silent(duration=target_ms)
        
        voice_map_dict = {m.speaker_id: m.voice_id for m in req.voice_map}
        
        # Controlled parallel synthesis with retry on API rate limits.
        def process_synthesis_block(index, block):
            text = block.get("transcript", "")
            if not text.strip():
                return index, None, 0, None
                
            speakers = block.get("speakers", [])
            timestamps = block.get("timestamps", [])
            
            primary_speaker = speakers[0] if speakers else "S0"
            sarvam_speaker = voice_map_dict.get(primary_speaker, "anushka")
            
            start_time = float(timestamps[0]) * 1000 if isinstance(timestamps, list) and timestamps else 0 
            
            clip_path = os.path.join(session_dir, f"clip_{index}.wav")
            try:
                print(f"Synthesizing block {index}: {text[:30]}...")
                audio_seg = generate_voice_clip_with_retry(text, sarvam_speaker, req.target_lang, clip_path)
                return index, audio_seg, start_time, None
            except Exception as e:
                print(f"Synthesis failed for block {index}: {e}")
                return index, None, start_time, str(e)

        with ThreadPoolExecutor(max_workers=SYNTHESIS_MAX_WORKERS) as executor:
            futures = [executor.submit(process_synthesis_block, i, block) for i, block in enumerate(req.transcript_blocks)]
            
            # Use as_completed to track progress or just wait
            results = []
            for future in as_completed(futures):
                results.append(future.result())
        
        # Sort results by index to maintain consistency (though not strictly necessary for overlay)
        results.sort(key=lambda x: x[0])
        
        failed_blocks = []
        for idx, audio_seg, start_time, error_text in results:
            if error_text:
                failed_blocks.append({"index": idx, "error": error_text})
            if audio_seg:
                mixed_audio = mixed_audio.overlay(audio_seg, position=start_time)
            
        output_filename = os.path.join(session_dir, "final.wav")
        # Do not strip silence so the duration maps 1:1 with the original video upload
        mixed_audio.export(output_filename, format="wav")
        
        final_video_url = None
        input_mp4 = os.path.join(session_dir, "input.mp4")
        if os.path.exists(input_mp4):
            final_mp4 = os.path.join(session_dir, "final.mp4")
            try:
                cmd = [
                    "ffmpeg", "-y", "-i", input_mp4, "-i", output_filename,
                    "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                    final_mp4
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"FFmpeg muxing error! Stderr: {result.stderr}")
                else:
                    final_video_url = f"/api/video/{session_id}"
            except Exception as fe:
                print(f"FFmpeg muxing exception: {fe}")
        
        # Return path or stream
        return JSONResponse(content={
            "audio_url": f"/api/audio/{session_id}",
            "video_url": final_video_url,
            "failed_block_count": len(failed_blocks),
            "failed_blocks": failed_blocks
        })
    except Exception as e:
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
