import os
from elevenlabs.client import ElevenLabs
from pydub import AudioSegment
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("ELEVEN_API_KEY")
if not api_key:
    raise RuntimeError("Missing ELEVEN_API_KEY in environment.")

# Initialize ElevenLabs
eleven_client = ElevenLabs(api_key=api_key)

# ==========================================
# 1. GENERATION & CLEANUP FUNCTION
# ==========================================
def create_voice_clip(text, voice_id, filename):
    print(f"Generating {filename}...")

    audio = eleven_client.text_to_speech.convert(
        voice_id=voice_id,
        output_format="mp3_44100_128",
        text=text,
        model_id="eleven_multilingual_v2"
    )

    # Save temporary file
    with open(filename, "wb") as f:
        for chunk in audio:  
            f.write(chunk)

    # Load into memory
    audio_segment = AudioSegment.from_mp3(filename)
    
    # 🔥 CLEANUP: Delete the individual file now that it's in memory
    if os.path.exists(filename):
        os.remove(filename)
        
    return audio_segment

# ==========================================
# 2. DEFINE THE VOICES & SCRIPT
# ==========================================
print("Starting audio generation...")

# We are using 7 distinct ElevenLabs voices (mix of male/female/accents)
voices = {
    "S1": "N2lVS1w4EtoT3dr4eOWO", # Callum (Male)
    "S2": "21m00Tcm4TlvDq8ikWAM", # Rachel (Female)
    "S3": "pqHfZKP75CvOlQylNhV4", # Bill (Male)
    "S4": "AZnzlk1XvdvUeBnXmlld", # Domi (Female)
    "S5": "pNInz6obpgDQGcFmaJgB", # Adam (Male)
    "S6": "ErXwobaYiN019PkySvjV", # Antoni (Male)
    "S7": "EXAVITQu4vr4xnSDxMaL"  # Bella (Female)
}

# Generate the clips
clip1 = create_voice_clip("Alright team, let's kick off this meeting. I want to review the deployment timeline for the new pipeline.", voices["S1"], "temp_1.mp3")
clip2 = create_voice_clip("Wait, before we talk about deployment, did anyone actually fix the memory leak in the staging environment?", voices["S2"], "temp_2.mp3")
clip3 = create_voice_clip("I looked into the memory leak yesterday. It's an issue with the batch processing queue holding onto references.", voices["S3"], "temp_3.mp3")
clip4 = create_voice_clip("Are you sure? Because I checked the logs and it looked like the database connection pool was maxing out.", voices["S4"], "temp_4.mp3")
clip5 = create_voice_clip("Guys, let's stay focused. We can debug the memory leak later. What is our hard deadline for production?", voices["S5"], "temp_5.mp3")
clip6 = create_voice_clip("The client expects the beta version by Friday, which means we have exactly three days to finalize the tests.", voices["S6"], "temp_6.mp3")
clip7 = create_voice_clip("Friday is impossible! We haven't even finished the diarization benchmarks on the new dataset!", voices["S7"], "temp_7.mp3")

# ==========================================
# 3. MIX THE CLIPS TOGETHER (THE CHAIN)
# ==========================================
print("Mixing audio tracks...")

# Create a 40-second blank canvas
mixed_audio = AudioSegment.silent(duration=40000)

# The Domino Effect Timeline (times in milliseconds)
# Notice how the start times force the clips to overlap with the previous speaker
mixed_audio = mixed_audio.overlay(clip1, position=0)       # S1 starts at 0s
mixed_audio = mixed_audio.overlay(clip2, position=4000)    # S2 interrupts S1 at 4s
mixed_audio = mixed_audio.overlay(clip3, position=8500)    # S3 takes over at 8.5s
mixed_audio = mixed_audio.overlay(clip4, position=12000)   # S4 interrupts S3 at 12s
mixed_audio = mixed_audio.overlay(clip5, position=17000)   # S5 takes over at 17s
mixed_audio = mixed_audio.overlay(clip6, position=21000)   # S6 interrupts S5 at 21s
mixed_audio = mixed_audio.overlay(clip7, position=25000)   # S7 interrupts S6 at 25s

# Trim the silence off the end
final_audio = mixed_audio.strip_silence(silence_len=1000, silence_thresh=-40)

# Export ONLY the final test file
output_filename = "sarvam_7_speaker_overlap_test.mp3"
final_audio.export(output_filename, format="mp3")

print(f"\nSuccess! Created complex test audio: {output_filename}")
print("All temporary individual files have been deleted.")