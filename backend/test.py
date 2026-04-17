from sarvamai import SarvamAI

client = SarvamAI(api_subscription_key="sk_h4mpdao3_s1DBD6ZHLQQC5FpPLsQS0lXC")

response = client.speech_to_text.translate(
    file=open("output1.mp3", "rb"),
    model="saaras:v3",
    source_language_code="en-IN",
    target_language_code="hi-IN"
)

print(response.transcript)