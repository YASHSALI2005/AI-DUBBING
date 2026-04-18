import yt_dlp

def download_yt_video(youtube_url, output_filename="test_video"):
    ydl_opts = {
        'format': 'bestvideo+bestaudio',
        'outtmpl': f'{output_filename}.%(ext)s',
        'merge_output_format': 'mp4',
        'noplaylist': True,
    }

    print(f"Downloading: {youtube_url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
    print("Done!")

target_url = "https://youtu.be/MWtEeuNatKw?si=i4Jy_fZRZF5uQrmH"
download_yt_video(target_url, "my_video4")