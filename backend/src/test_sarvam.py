import os
import json
from sarvamai import SarvamAI
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

# Initialize the client using environment variable
client = SarvamAI(api_subscription_key=os.getenv("SARVAM_API_KEY"))

# 1. Create an asynchronous batch job
# 1. Create an asynchronous batch job
print("Creating job...")
job = client.speech_to_text_job.create_job(
    model="saaras:v3",
    mode="transcribe", 
    with_diarization=True,   # <--- ADD THIS LINE
    with_timestamps=True     # <--- ADD THIS LINE (Optional, but highly recommended)
)

# 2. Upload your file
print("Uploading file...")
job.upload_files(file_paths=["output4.mp3"])

# 3. Start the transcription process
job.start()

# 4. Wait for it to finish processing (this can take a few minutes)
print("Processing... waiting for completion.")
job.wait_until_complete()

# 5. Fetch and print the status/results
status = client.speech_to_text_job.get_status(job_id=job.job_id)
print(status)
# 5. Download the result to a local folder
print("Job complete! Downloading results...")
output_folder = "transcription_results"
os.makedirs(output_folder, exist_ok=True)

# This grabs the generated '0.json' file from the cloud
job.download_outputs(output_dir=output_folder)

# 6. Read the JSON and print the transcript
result_file = os.path.join(output_folder, "0.json")

if os.path.exists(result_file):
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
        # Grab the raw transcript string from the JSON payload
        transcript = data.get("transcript", "No transcript found.")
        
        print("\n" + "="*30)
        print("YOUR TRANSCRIPT:")
        print("="*30)
        print(transcript)
        print("="*30)
else:
    print(f"Error: Could not find the result file at {result_file}")