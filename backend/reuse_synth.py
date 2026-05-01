import json
import pathlib
import requests
import sys

old_id = sys.argv[1]
new_id = sys.argv[2]

old_dir = pathlib.Path("D:/test/backend/temp") / old_id
analysis = json.loads((old_dir / "analysis.json").read_text(encoding="utf-8"))

blocks = [
    {
        "id": f"block-{i}",
        "speakers": [s.get("speaker", "S0")],
        "transcript": s.get("tagged_text") or s.get("text", ""),
        "timestamps": [float(s.get("start", 0.0) or 0.0), float(s.get("end", 0.0) or 0.0)],
    }
    for i, s in enumerate(analysis.get("segments", []))
]

voice_map = [
    {
        "speaker_id": sp.get("name"),
        "voice_id": sp.get("recommended_voice") or "Aoede",
    }
    for sp in analysis.get("speakers", [])
    if sp.get("name")
]

payload = {
    "session_id": new_id,
    "transcript_blocks": blocks,
    "voice_map": voice_map,
    "speaker_genders": {},
    "target_duration_ms": 0,
    "target_lang": "hi-IN",
    "auto_detect_speakers": False,
    "disable_voice_cloning": False,
    "synthesis_mode": "batched_per_speaker_gemini",
}

r = requests.post("http://127.0.0.1:8000/api/synthesize", json=payload, timeout=36000)
print(r.status_code)
print(r.text[:1200])
