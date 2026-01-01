import os
import sys
import asyncio
import yt_dlp
from urllib.parse import urlparse
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
import config
from modules.utils.validator import UrlValidator
from modules.utils.exceptions import DownloadCancelled

# Cache for YouTube selection
youtube_selection_cache = {}

async def show_youtube_selection(client, message, url):
    msg = await message.reply("Fetching available formats...")
    youtube_selection_cache[msg.id] = url

    def get_info():
        with yt_dlp.YoutubeDL() as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(get_info)

        buttons = []
        # Filter formats
        formats = info.get('formats', [])
        # Get unique heights for video
        resolutions = set()
        for f in formats:
            if f.get('vcodec') != 'none' and f.get('height'):
                resolutions.add(f['height'])

        # Sort resolutions descending
        sorted_res = sorted(list(resolutions), reverse=True)

        for res in sorted_res:
            btn_text = f"{res}p"
            buttons.append(InlineKeyboardButton(btn_text, callback_data=f"yt|video|{res}"))

        # Add Audio button
        buttons.append(InlineKeyboardButton("Audio (MP3)", callback_data="yt|audio"))

        # Layout buttons
        keyboard = []
        row = []
        for btn in buttons:
            row.append(btn)
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        markup = InlineKeyboardMarkup(keyboard)
        await msg.edit("Select quality:", reply_markup=markup)
        return {"status": "interaction_required", "message_id": msg.id}

    except Exception as e:
        await msg.edit(f"Error fetching formats: {e}")
        return {"status": "error", "message": str(e)}

async def download(url: str, client, message, progress_callback, user_manager, video_id, audio=False, format_id="bestvideo+bestaudio/best", custom_title=None):
    output_folder = config.output_folder
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    url_info = urlparse(url)

    # Auto-detect audio mode for music platforms
    if not audio:
        domain = url_info.netloc.lower()
        if any(x in domain for x in ['soundcloud.com', 'mixcloud.com', 'bandcamp.com']):
            audio = True

    if url_info.scheme:
        if url_info.netloc in ['www.youtube.com', 'youtu.be', 'youtube.com', 'youtu.be']:
            if not UrlValidator(url).isYouTube():
                return {"status": "error", "message": "Invalid URL"}

            # Show quality selection for YouTube if default format
            if format_id == "bestvideo+bestaudio/best" and not audio:
                # Check user preference
                user_id = message.from_user.id if message.from_user else 0
                pref = user_manager.get_quality(user_id)

                if pref == "ask":
                    return await show_youtube_selection(client, message, url)
                elif pref == "audio":
                    audio = True
                    # Fall through to download
                else:
                    # Set specific quality (Prioritize H.264/AAC for compatibility)
                    if pref == "best":
                        format_id = "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo+bestaudio/best"
                    else:
                        # e.g. 720p -> bestvideo[height<=720][vcodec^=avc1]+...
                        try:
                            res = int(pref.replace("p", ""))
                            format_id = f"bestvideo[height<={res}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[height<={res}]+bestaudio/best[height<={res}]"
                        except ValueError:
                            format_id = "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo+bestaudio/best"

    return await download_real(url, video_id, audio, format_id, progress_callback)

async def download_real(url, video_id, audio, format_id, progress_callback):
    output_path = f'{config.output_folder}/{video_id}.%(ext)s'

    ydl_opts = {
        'format': format_id,
        'outtmpl': output_path,
        'progress_hooks': [progress_callback],
        'max_filesize': config.max_filesize,
        'http_chunk_size': 10485760, # 10MB
        'remote_components': {'ejs:github'},
        'concurrent_fragment_downloads': 10,
        'quiet': False,
        'noprogress': False,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 10,
        'buffersize': 1024 * 1024 * 10,
        'noplaylist': True,
    }

    if audio:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['writethumbnail'] = True
        ydl_opts['postprocessors'] = [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            },
            {'key': 'EmbedThumbnail'},
            {'key': 'FFmpegMetadata'},
        ]
    else:
        ydl_opts['merge_output_format'] = 'mp4'

    def run_yt_dlp():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    try:
        info = await asyncio.to_thread(run_yt_dlp)

        # Determine filepath
        filepath = None
        if 'requested_downloads' in info:
            filepath = info['requested_downloads'][0]['filepath']
        else:
             # Fallback
            for file in os.listdir(config.output_folder):
                if file.startswith(video_id):
                    filepath = os.path.join(config.output_folder, file)
                    break

        return {
            "status": "success",
            "isUrl": False,
            "filepath": filepath,
            "filename": os.path.basename(filepath) if filepath else None,
            "description": info.get('description', ''),
            "title": info.get('fulltitle', ''),
            "thumbnail": info.get('thumbnail', ''),
            "resolution": info.get('resolution', 'NonexNone') if not audio else None,
            "duration": info.get('duration'),
            "original_url": info.get('webpage_url', url),
            "acodec": info.get('acodec', 'Unknown') if audio else None,
            "vcodec": info.get('vcodec', 'Unknown') if not audio else None,
            "ext": info.get('ext', 'mp3' if audio else 'mp4'),
            "size": info.get('filesize_approx') or info.get('filesize') or 0,
            # "info": info,
            "type": "audio" if audio else "video"
        }

    except Exception as e:
        # Re-raise DownloadCancelled so it propagates to main.py
        if isinstance(e, DownloadCancelled) or "Bot shutting down" in str(e):
            raise e
        return {"status": "error", "message": str(e)}
