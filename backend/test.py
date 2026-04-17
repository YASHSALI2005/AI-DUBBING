from sarvamai import SarvamAI
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("SARVAM_API_KEY")
if not api_key:
    raise RuntimeError("Missing SARVAM_API_KEY in environment.")

client = SarvamAI(api_subscription_key=api_key)

response = client.speech_to_text.translate(
    file=open("output1.mp3", "rb"),
    model="saaras:v3",
    source_language_code="en-IN",
    target_language_code="hi-IN"
)

print(response.transcript)