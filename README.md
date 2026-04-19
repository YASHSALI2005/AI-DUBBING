# AI Dubbing Pipeline

A premium, full-stack AI-powered video dubbing application that automates the entire process of translating and re-voicing videos while maintaining emotional context.

## 🚀 Overview
This project provides a seamless pipeline to dub videos from one language to another. It leverages state-of-the-art AI models from **Sarvam AI** and **Google Gemini** to ensure high-quality transcription, natural translation, and emotionally resonant text-to-speech.

## ✨ Key Features
- **Automated Transcription**: Uses Sarvam AI (saaras:v3) for high-accuracy Speech-to-Text with speaker diarization.
- **Contextual Translation**: Translates transcripts using Sarvam AI (sarvam-translate:v1), preserving meaning and speaker-specific nuances.
- **Emotion Enhancement**: Leverages Google Gemini to analyze original audio chunks and insert emotion tags (e.g., `[excitedly]`, `[whispers]`) into the translated transcript.
- **Dynamic TTS Synthesis**: Generates dubbed audio using Gemini's native TTS engine, with automatic voice mapping based on speaker gender and characteristics.
- **Timeline Mixing**: Precision-overlays synthesized audio onto the original video timeline, ensuring synchronization.
- **Video Muxing**: Automatically replaces the original audio track with the dubbed audio using FFmpeg.

## 🛠️ Tech Stack
### Backend
- **Framework**: FastAPI (Python)
- **AI Services**: Sarvam AI SDK, Google Gemini API
- **Audio Processing**: Pydub, FFmpeg
- **Concurrency**: AsyncIO with ThreadPoolExecutor for high-performance parallel processing.

### Frontend
- **Framework**: React + Vite
- **Styling**: Tailwind CSS, Lucide React
- **Animations**: Framer Motion
- **State Management**: React Hooks (useState/useEffect)

## 🔄 Workflow Pipeline
1. **Upload**: User uploads a video. The system extracts audio and runs an asynchronous Sarvam STT job.
2. **Translate**: Transcript blocks are translated into the target language.
3. **Enhance**: Gemini analyzes the original audio and adds emotional markers to the text.
4. **Synthesize**: The system generates audio for each block and mixes it into a final track.
5. **Download**: The final dubbed video is ready for preview and download.

## ⚙️ Setup & Installation

### Backend
1. Navigate to `/backend`.
2. Install dependencies: `pip install -r requirements.txt`.
3. Create a `.env` file with:
   ```env
   SARVAM_API_KEY=your_key
   GEMINI_API_KEY=your_key
   ```
4. Run the server: `python -m uvicorn app:app --reload`.

### Frontend
1. Navigate to `/frontend`.
2. Install dependencies: `npm install`.
3. Run the dev server: `npm run dev`.

## 📜 Endpoints
- `POST /api/upload`: Handles video upload and triggers STT.
- `POST /api/translate`: Translates transcription blocks.
- `POST /api/enhance-translation`: Adds emotional context using Gemini.
- `POST /api/synthesize`: Finalizes audio/video generation.
