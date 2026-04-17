import os
from elevenlabs.client import ElevenLabs
from pydub import AudioSegment

# Initialize ElevenLabs
eleven_client = ElevenLabs(api_key="sk_21346e51dedd54805124b6a7781240f96c79eccbc0d6f6d0")

# ==========================================
# 1. GENERATE THE RAW AUDIO CLIPS
# ==========================================
def create_voice_clip(text, voice_name, filename):
    print(f"Generating {filename} ({voice_name})...")

    audio = eleven_client.text_to_speech.convert(
        voice_id=voice_name,
        output_format="mp3_44100_128",
        text=text,
        model_id="eleven_multilingual_v2"
    )

    with open(filename, "wb") as f:
        for chunk in audio:   # 🔥 FIX HERE
            f.write(chunk)

    return AudioSegment.from_mp3(filename)

print("Starting audio generation...")

# Speaker 1 (Standard Male Voice, e.g., 'Callum')
s1_intro = create_voice_clip(
    "Hi, I am the first speaker. I will be talking for roughly five seconds to establish my baseline voice.", 
    "N2lVS1w4EtoT3dr4eOWO", 
    "s1_intro.mp3"
)

# Speaker 2 (Standard Female Voice, e.g., 'Rachel')
s2_intro = create_voice_clip(
    "Hello, I am the second speaker. I am stepping in now to give the model a completely different vocal profile.", 
    "21m00Tcm4TlvDq8ikWAM", 
    "s2_intro.mp3"
)

# The Overlap Dialogue
s1_overlap = create_voice_clip(
    "Now I am going to keep talking about the project while the other person completely interrupts my sentence.", 
    "N2lVS1w4EtoT3dr4eOWO", 
    "s1_overlap.mp3"
)
s2_overlap = create_voice_clip(
    "I am interrupting you right now to see if the Sarvam model can actually handle two people talking at exactly the same time.", 
    "21m00Tcm4TlvDq8ikWAM", 
    "s2_overlap.mp3"
)

# ==========================================
# 2. MIX THE CLIPS TOGETHER WITH OVERLAPS
# ==========================================
print("Mixing audio tracks...")

# Create a blank, silent canvas of 20 seconds (20,000 milliseconds)
mixed_audio = AudioSegment.silent(duration=20000)

# Position 1: Speaker 1 starts at 0 seconds
mixed_audio = mixed_audio.overlay(s1_intro, position=0)

# Position 2: Speaker 2 starts at 5 seconds (5000 ms)
mixed_audio = mixed_audio.overlay(s2_intro, position=5000)

# Position 3: The Overlap! 
# Speaker 1 starts at 11 seconds, Speaker 2 jumps in at 11.5 seconds
mixed_audio = mixed_audio.overlay(s1_overlap, position=11000)
mixed_audio = mixed_audio.overlay(s2_overlap, position=11500)

# Trim the silence off the end (keeps it neat)
final_audio = mixed_audio.strip_silence(silence_len=1000, silence_thresh=-40)

# Export the final test file
output_filename = "sarvam_diarization_test_01.mp3"
final_audio.export(output_filename, format="mp3")

print(f"\nSuccess! Created perfectly timed test audio: {output_filename}")