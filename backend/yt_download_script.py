from pathlib import Path

import yt_dlp


class MyLogger:
    def debug(self, msg):
        # Show ffmpeg live progress lines when range-download uses ffmpeg downloader.
        if "frame=" in msg or "size=" in msg or "time=" in msg:
            clean_msg = msg.replace("[debug] ", "").strip()
            print(f"\r{clean_msg}", end=" " * 8, flush=True)
            return
        # Print useful non-debug lines as-is.
        if not msg.startswith("[debug]"):
            print(msg, flush=True)

    def info(self, msg):
        print(msg, flush=True)

    def warning(self, msg):
        print(msg, flush=True)

    def error(self, msg):
        print(msg, flush=True)


def progress_hook(data):
    status = data.get("status")
    if status == "downloading":
        pct = data.get("_percent_str", "").strip()
        speed = data.get("_speed_str", "").strip()
        eta = data.get("_eta_str", "").strip()
        print(f"\r[download] {pct} | {speed} | ETA {eta}", end=" " * 8, flush=True)
    elif status == "finished":
        print("\n[download] finished, processing...", flush=True)


def download_yt_video(url, output_filename="video"):
    Path("input").mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        # For range-trim stability, prefer a single progressive mp4 stream.
        # DASH split streams + remote seek can fail with "partial file / could not seek".
        'format': 'best[ext=mp4]/best',
        'merge_output_format': 'mp4',

        # Save output inside the 'input' folder
        'outtmpl': f'input/{output_filename}.%(ext)s',
        'noplaylist': True,

        # ✅ trim
        'download_ranges': lambda info, ydl: [{
            'start_time': 540,
            'end_time': 1200
        }],
        'force_keyframes_at_cuts': True,
        'retries': 10,
        'fragment_retries': 10,

        # Clean up any unmerged/part files if they get stuck
        'keepvideo': False,
        'clean_infojson': True,

        # Progress output
        'progress_hooks': [progress_hook],
        'logger': MyLogger(),
        'quiet': False,
        'no_warnings': False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print("\nDownload and processing complete!", flush=True)


if __name__ == "__main__":
    download_yt_video("https://youtu.be/tnvjsdOP62k?si=DN4jk2BlCNUdeMF-", "my_video9_trim")