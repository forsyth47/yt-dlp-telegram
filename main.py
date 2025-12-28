import os
import re
import time
import datetime
import asyncio
import uuid
import shutil
import json
from urllib.parse import urlparse

# Fix for Python 3.10+ where get_event_loop() raises RuntimeError if no loop is set
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import yt_dlp
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified

import config
import log as logger
from users import UserManager

# Try to import Redis client
try:
    from redis_client import r as redis_client
    REDIS_AVAILABLE = True
    print("✅ Redis client loaded successfully.")
except Exception as e:
    REDIS_AVAILABLE = False
    redis_client = None
    # print(f"⚠️ Redis client could not be loaded: {e}")

# Initialize the Pyrogram Client
app = Client(
    "yt_dlp_bot",
    api_id=config.api_id,
    api_hash=config.api_hash,
    bot_token=config.token,
    workers=50, # Allow more concurrent update handlers
    max_concurrent_transmissions=10 # Allow multiple files to be uploaded simultaneously
)

user_manager = UserManager()

STOP_REQUESTED = False
MESSAGE_UPDATE_INTERVAL = 10
last_edited = {}
active_downloads = {}
download_progress = {}
youtube_selection_cache = {}

class DownloadCancelled(Exception):
    def __init__(self, action):
        self.action = action

# Helper to format bytes
def format_bytes(b):
    if not b: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024: return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"

# Helper to format time
def format_time(seconds):
    if not seconds: return "0s"
    return str(datetime.timedelta(seconds=int(seconds)))

def youtube_url_validation(url):
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')

    youtube_regex_match = re.match(youtube_regex, url)
    if youtube_regex_match:
        return youtube_regex_match

    return youtube_regex_match

@app.on_message(filters.command(['start', 'help']))
async def start_command(client: Client, message: Message):
    print(f"Start command received. Args: {message.command}, Redis Available: {REDIS_AVAILABLE}")
    # Check for arguments (Redis short code)
    if len(message.command) > 1 and REDIS_AVAILABLE:
        token = message.command[1]
        key = f"dl:{token}"
        print(f"Checking Redis for key: {key}")

        try:
            raw = redis_client.get(key)
            print(f"Redis result: {raw}")
            if raw:
                data = json.loads(raw)
                redis_client.delete(key) # One-time use

                url = data.get('url')
                title = data.get('title')

                if url:
                    await message.reply(f"📥 **Found download:**\n`{title}`")
                    asyncio.create_task(download_video(message, url, custom_title=title))
                    return
                else:
                    await message.reply("❌ Invalid data in link.")
                    return
            else:
                await message.reply("❌ Link expired or invalid.")
                return
        except Exception as e:
            print(f"Redis error: {e}")

    await message.reply(
        "**Send me a video link** and I'll download it for you, works with **YouTube**, **Twitter**, **TikTok**, **Reddit** and more.\n\n_Powered by_ [yt-dlp](https://github.com/yt-dlp/yt-dlp/)",
        disable_web_page_preview=True
    )

@app.on_message(filters.command(['id']))
async def get_id(client: Client, message: Message):
    chat = message.chat
    await message.reply(f"**Chat ID:** `{chat.id}`\n**Type:** {chat.type}")

async def show_youtube_selection(message, url):
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

    except Exception as e:
        await msg.edit(f"Error fetching formats: {e}")

async def download_video(message: Message, url, audio=False, format_id="bestvideo+bestaudio/best", custom_title=None):
    url_info = urlparse(url)

    # Auto-detect audio mode for music platforms
    if not audio:
        domain = url_info.netloc.lower()
        if any(x in domain for x in ['soundcloud.com', 'mixcloud.com', 'bandcamp.com']):
            audio = True

    if url_info.scheme:
        if url_info.netloc in ['www.youtube.com', 'youtu.be', 'youtube.com', 'youtu.be']:
            if not youtube_url_validation(url):
                await message.reply('Invalid URL')
                return

            # Show quality selection for YouTube if default format
            if format_id == "bestvideo+bestaudio/best" and not audio:
                # Check user preference
                user_id = message.from_user.id if message.from_user else 0
                pref = user_manager.get_quality(user_id)

                if pref == "ask":
                    await show_youtube_selection(message, url)
                    return
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

                    # Fall through to download with new format_id

        # Use UUID for unique filenames to prevent collisions between users
        video_id = str(uuid.uuid4())
        active_downloads[video_id] = {'action': None, 'last_info': None}
        download_progress[video_id] = {'status': 'starting', 'downloaded': 0, 'total': 0, 'speed': 0, 'eta': 0, 'title': 'Video', 'ext': 'mp4'}

        await logger.log(app, message, f"Starting download: {url} (ID: {video_id})", level="INFO")

        # Buttons
        cancel_btn = InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|del|{video_id}")
        send_btn = InlineKeyboardButton("📤 Send Partial", callback_data=f"cancel|send|{video_id}")
        keyboard = InlineKeyboardMarkup([[cancel_btn, send_btn]])

        # Send GIF
        gif_msg = await message.reply_animation("https://media.tenor.com/akRQReAe9JoAAAAM/walter-white-let-him-cook.gif")

        # Send Tip/Status Message
        tip_text = "__Initializing download... Please wait while I cook.__"
        if 'pref' in locals() and pref != "ask":
             tip_text = f"__Video will be downloaded at `{pref}` quality. Visit /settings to update.__\n\n{tip_text}"

        msg = await message.reply(tip_text)

        loop = asyncio.get_running_loop()
        gif_deleted = False

        # Start progress update task
        async def update_progress_message():
            last_update_time = 0
            while video_id in active_downloads and not STOP_REQUESTED:
                try:
                    now = time.time()
                    if now - last_update_time < MESSAGE_UPDATE_INTERVAL:
                        await asyncio.sleep(1)
                        continue

                    prog = download_progress.get(video_id)
                    if not prog or prog['status'] != 'downloading':
                        await asyncio.sleep(1)
                        continue

                    # Delete GIF if needed
                    nonlocal gif_deleted
                    if not gif_deleted:
                        try:
                            await gif_msg.delete()
                            gif_deleted = True
                        except Exception:
                            pass

                    last_update_time = now

                    title = prog.get('title', 'Video')
                    ext = prog.get('ext', 'mp4')
                    total = prog.get('total', 0)
                    downloaded = prog.get('downloaded', 0)
                    speed = prog.get('speed', 0)
                    eta = prog.get('eta', 0)

                    if total:
                        percentage = downloaded * 100 / total
                        progress_str = f"{percentage:.1f}%"
                        total_str = format_bytes(total)
                    else:
                        progress_str = "N/A"
                        total_str = "N/A"

                    downloaded_str = format_bytes(downloaded)
                    speed_str = f"{format_bytes(speed)}/s" if speed else "N/A"
                    eta_str = format_time(eta) if eta else "N/A"

                    text = (
                        f"Downloading: `{title}.{ext}`\n\n"
                        f"💾 Size: {downloaded_str} / {total_str}\n"
                        f"📊 Progress: {progress_str}\n"
                        f"🚀 Speed: {speed_str}\n"
                        f"⏳ ETA: {eta_str}"
                    )

                    try:
                        await msg.edit(text, reply_markup=keyboard)
                    except Exception:
                        pass

                except Exception as e:
                    print(f"Error in update loop: {e}")

                await asyncio.sleep(1)

        progress_task = asyncio.create_task(update_progress_message())

        # Progress hook for yt-dlp (runs in a thread)
        def progress(d):
            if STOP_REQUESTED:
                raise Exception("Bot shutting down")

            # Check for cancellation
            if active_downloads.get(video_id, {}).get('action'):
                action = active_downloads[video_id]['action']
                if action == 'send':
                    # Try to preserve the file
                    try:
                        current_file = d.get('filename')
                        # Sometimes the file on disk has a .part extension
                        if current_file and not os.path.exists(current_file):
                            if os.path.exists(current_file + ".part"):
                                current_file += ".part"

                        if current_file and os.path.exists(current_file):
                            # Copy to a safe partial name
                            partial_path = f'{config.output_folder}/{video_id}_partial.mp4'
                            shutil.copy2(current_file, partial_path)
                        else:
                            print(f"Could not find file to copy: {d.get('filename')}")
                    except Exception as e:
                        print(f"Failed to copy partial file: {e}")
                raise DownloadCancelled(action)

            if d['status'] == 'downloading':
                try:
                    # Update shared state
                    if video_id in download_progress:
                        download_progress[video_id].update({
                            'status': 'downloading',
                            'title': d.get('info_dict', {}).get('title', 'Video'),
                            'ext': d.get('info_dict', {}).get('ext', 'mp4'),
                            'total': d.get('total_bytes') or d.get('total_bytes_estimate'),
                            'downloaded': d.get('downloaded_bytes', 0),
                            'speed': d.get('speed'),
                            'eta': d.get('eta'),
                            'info_dict': d.get('info_dict') # Store for partial send
                        })

                        # Also update active_downloads for partial send logic
                        if active_downloads.get(video_id):
                            active_downloads[video_id]['last_info'] = d.get('info_dict')

                except Exception as e:
                    print(f"Error in progress hook: {e}")
        if not os.path.exists(config.output_folder):
            os.makedirs(config.output_folder)

        output_path = f'{config.output_folder}/{video_id}.%(ext)s'

        ydl_opts = {
            'format': format_id,
            'outtmpl': output_path,
            'progress_hooks': [progress],
            'max_filesize': config.max_filesize,
            'http_chunk_size': 10485760, # 10MB
            'remote_components': {'ejs:github'},
            'concurrent_fragment_downloads': 10,
            'quiet': False,
            'noprogress': False,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 10, # Retry stuck fragments quickly
            'buffersize': 1024 * 1024 * 10, # 10MB buffer
        }

        if audio:
            # Force best audio for audio-only downloads
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
            # Merge to mp4 for video downloads
            ydl_opts['merge_output_format'] = 'mp4'

        # Run blocking yt-dlp code in a separate thread to avoid blocking the event loop
        def run_yt_dlp():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        filepath = None
        info = None

        try:
            info = await asyncio.to_thread(run_yt_dlp)

            if custom_title:
                info['title'] = custom_title

            # Stop progress task
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            # Find the downloaded file
            if 'requested_downloads' in info:
                filepath = info['requested_downloads'][0]['filepath']
            else:
                # Fallback: look for file starting with video_id
                for file in os.listdir(config.output_folder):
                    if file.startswith(video_id):
                        filepath = os.path.join(config.output_folder, file)
                        break

        except DownloadCancelled as e:
            # Stop progress task
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            if e.action == 'del':
                await msg.edit("❌ Download cancelled.")
                await logger.log(app, message, f"Download cancelled by user: {video_id}", level="WARNING")
                return
            elif e.action == 'send':
                await msg.edit("📤 Processing partial download...")
                await logger.log(app, message, f"Partial download requested: {video_id}", level="INFO")
                filepath = f'{config.output_folder}/{video_id}_partial.mp4'
                info = active_downloads.get(video_id, {}).get('last_info', {})
                if not info:
                    info = {'title': 'Partial Download', 'ext': 'mp4'}

        except yt_dlp.utils.DownloadError as e:
            # Stop progress task
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            await msg.edit('Invalid URL or download error.')
            await logger.log(app, message, f"Download error: {e}", level="ERROR")
            return
        except Exception as e:
            # Stop progress task
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            print(f"General error: {e}")
            await msg.edit(f"Error: {e}")
            await logger.log(app, message, f"General error: {e}", level="ERROR")
            return

        if not filepath or not os.path.exists(filepath):
            await msg.edit("Could not find downloaded file.")
            await logger.log(app, message, f"File not found after download: {video_id}", level="ERROR")
            return

        await msg.edit('Sending file to Telegram...')
        await logger.log(app, message, f"Download complete, uploading: {filepath}", level="INFO")

        # Upload progress
        async def upload_progress(current, total):
            try:
                now = time.time()
                key = f"{message.chat.id}-{msg.id}-upload"

                if key in last_edited:
                    if now - last_edited[key] < MESSAGE_UPDATE_INTERVAL:
                        return

                last_edited[key] = now

                perc = round(current * 100 / total)
                await msg.edit(f"Uploading to Telegram...\n\n{perc}%")
            except Exception:
                pass        # Generate caption
        title = info.get('title', 'Unknown')
        original_url = info.get('webpage_url', url)
        file_size = os.path.getsize(filepath)
        size_str = format_bytes(file_size)

        # Rename audio file to title
        if audio:
            try:
                safe_title = "".join([c for c in title if c.isalnum() or c in [' ', '-', '_']]).strip()
                if not safe_title: safe_title = "audio"
                ext = os.path.splitext(filepath)[1]
                new_filename = f"{safe_title}{ext}"
                new_filepath = os.path.join(config.output_folder, new_filename)
                os.rename(filepath, new_filepath)
                filepath = new_filepath
            except Exception as e:
                print(f"Rename error: {e}")

        if audio:
            ext = info.get('ext', 'mp3')
            duration = format_time(info.get('duration'))
            acodec = info.get('acodec', 'Unknown')

            caption = (
                f"🎵 **{title}.{ext}**\n\n"
                f"⏱ **Duration:** {duration}\n"
                f"💾 **Size:** {size_str}\n"
                f"🔊 **Codec:** {acodec}\n\n"
                f"🔗 [Original Link]({original_url})"
            )
        else:
            ext = info.get('ext', 'mp4')
            resolution = info.get('resolution') or f"{info.get('width')}x{info.get('height')}"
            fps = info.get('fps')
            vcodec = info.get('vcodec', 'Unknown')
            acodec = info.get('acodec', 'Unknown')
            duration = format_time(info.get('duration'))

            caption = (
                f"📹 **{title}.{ext}**\n\n"
                f"📐 **Resolution:** {resolution}\n"
                f"⏱ **Duration:** {duration}\n"
                f"💾 **Size:** {size_str}\n"
                f"🎞 **FPS:** {fps}\n"
                f"⚙️ **Codec:** {vcodec} (Video) / {acodec} (Audio)\n\n"
                f"🔗 [Original Link]({original_url})"
            )

        try:
            if audio:
                performer = info.get('artist') or info.get('uploader') or info.get('creator') or 'Unknown'
                duration = int(info.get('duration') or 0)
                await message.reply_audio(
                    audio=filepath,
                    caption=caption,
                    progress=upload_progress,
                    title=title,
                    performer=performer,
                    duration=duration
                )
            else:
                width = int(info.get('width') or 0)
                height = int(info.get('height') or 0)
                duration = int(info.get('duration') or 0)

                await message.reply_video(
                    video=filepath,
                    caption=caption,
                    width=width,
                    height=height,
                    duration=duration,
                    progress=upload_progress,
                    supports_streaming=True
                )

            await msg.delete()
            await logger.log(app, message, f"Upload completed successfully: {title}", level="SUCCESS")
        except Exception as e:
            print(f"Upload error: {e}")
            await msg.edit(f"Couldn't send file. Error: {e}")
            await logger.log(app, message, f"Upload failed: {e}", level="ERROR")
        finally:
            # Cleanup
            if video_id in active_downloads:
                del active_downloads[video_id]
            if video_id in download_progress:
                del download_progress[video_id]

            # Remove main file (renamed or original)
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

            # Remove main file (if not renamed)
            for file in os.listdir(config.output_folder):
                if file.startswith(video_id):
                    try:
                        os.remove(os.path.join(config.output_folder, file))
                    except Exception:
                        pass

            # Remove partial file if it exists
            partial_path = f'{config.output_folder}/{video_id}_partial.mp4'
            if os.path.exists(partial_path):
                try:
                    os.remove(partial_path)
                except Exception:
                    pass

            # Cleanup last_edited keys
            keys_to_remove = [k for k in last_edited.keys() if str(msg.id) in k]
            for k in keys_to_remove:
                last_edited.pop(k, None)

    else:
        await message.reply('Invalid URL')
def get_text(message: Message):
    if not message:
        return None

    text = message.text or message.caption
    if not text:
        return None

    if len(text.split(' ')) < 2:
        if message.reply_to_message:
            return message.reply_to_message.text or message.reply_to_message.caption
        else:
            return None
    else:
        return text.split(' ', 1)[1]

@app.on_message(filters.command(['download']))
async def download_command(client, message):
    text = get_text(message)
    if not text:
        await message.reply('Invalid usage, use `/download url`')
        return

    await logger.log(app, message, f"Download command received: {text}", level="INFO")
    asyncio.create_task(download_video(message, text))

@app.on_message(filters.command(['audio']))
async def download_audio_command(client, message):
    text = get_text(message)
    if not text:
        await message.reply('Invalid usage, use `/audio url`')
        return

    await logger.log(app, message, f"Audio command received: {text}", level="INFO")
    asyncio.create_task(download_video(message, text, True))

@app.on_message(filters.command(['sendVideo']))
async def send_video_command(client, message):
    text = get_text(message)
    if not text:
        await message.reply('Invalid usage, use `/sendVideo url`')
        return

    await logger.log(app, message, f"SendVideo command received: {text}", level="INFO")

    msg = await message.reply("Sending video...")
    try:
        await message.reply_video(video=text, caption=f"🔗 [Original Link]({text})")
        await msg.delete()
        await logger.log(app, message, f"SendVideo success: {text}", level="SUCCESS")
    except Exception as e:
        await msg.edit(f"Failed to send video. Error: {e}")
        await logger.log(app, message, f"SendVideo failed: {e}", level="ERROR")

@app.on_message(filters.command(['settings']))
async def settings_command(client, message):
    user_id = message.from_user.id
    current_pref = user_manager.get_quality(user_id)

    text = f"⚙️ **Settings**\n\nCurrent Quality Preference: `{current_pref}`\n\nSelect your preferred default quality for YouTube downloads:"

    buttons = [
        [InlineKeyboardButton("Always Ask", callback_data="set|quality|ask")],
        [InlineKeyboardButton("Best Available", callback_data="set|quality|best")],
        [InlineKeyboardButton("1080p", callback_data="set|quality|1080p"), InlineKeyboardButton("720p", callback_data="set|quality|720p")],
        [InlineKeyboardButton("480p", callback_data="set|quality|480p"), InlineKeyboardButton("360p", callback_data="set|quality|360p")],
        [InlineKeyboardButton("Audio Only (MP3)", callback_data="set|quality|audio")]
    ]

    await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r"^set\|quality\|"))
async def set_quality_callback(client, call: CallbackQuery):
    quality = call.data.split("|")[2]
    user_id = call.from_user.id

    user_manager.set_quality(user_id, quality)

    await call.answer(f"Preference saved: {quality}")
    await call.message.edit(f"✅ **Settings Updated**\n\nDefault Quality: `{quality}`")

@app.on_callback_query(filters.regex(r"^cancel\|"))
async def cancel_download(client, call: CallbackQuery):
    data = call.data.split("|")
    action = data[1]
    vid = data[2]

    if vid in active_downloads:
        active_downloads[vid]['action'] = action
        await call.answer("Cancelling...")
        await call.message.edit("Cancelling...")
    else:
        await call.answer("Download not active or already finished.")

@app.on_callback_query(filters.regex(r"^yt\|"))
async def yt_callback(client, call: CallbackQuery):
    data = call.data.split("|")
    type = data[1]

    url = youtube_selection_cache.get(call.message.id)
    original_message = call.message.reply_to_message

    if not url:
        if original_message:
            url = get_text(original_message) or original_message.text or original_message.caption

    if not url:
        await call.answer("Could not find URL.", show_alert=True)
        return

    target_message = original_message if original_message else call.message

    if target_message == original_message:
        await call.message.delete()
    else:
        await call.message.edit("Processing selection...")

    # Clean cache
    youtube_selection_cache.pop(call.message.id, None)

    await logger.log(app, call.message, f"YouTube selection made: {type} for {url}", level="INFO")

    if type == "audio":
        asyncio.create_task(download_video(target_message, url, audio=True))
    elif type == "video":
        res = data[2]
        # Select specific resolution + best audio (Prioritize H.264/AAC)
        fmt = f"bestvideo[height={res}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[height={res}]+bestaudio/best[height={res}]"
        asyncio.create_task(download_video(target_message, url, audio=False, format_id=fmt))

@app.on_callback_query()
async def callback(client, call: CallbackQuery):
    if call.message.reply_to_message and call.from_user.id == call.message.reply_to_message.from_user.id:
        url = get_text(call.message.reply_to_message)
        await call.message.delete()
        asyncio.create_task(download_video(call.message.reply_to_message, url, format_id=f"{call.data}+bestaudio"))
    else:
        await call.answer("You didn't send the request", show_alert=True)

@app.on_message(filters.private & ~filters.command(['start', 'help', 'download', 'audio', 'custom', 'sendVideo']))
async def handle_private_messages(client, message):
    text = message.text or message.caption
    if not text:
        return

    await logger.log(app, message, f"Private message received: {text}", level="INFO")
    asyncio.create_task(download_video(message, text))

if __name__ == "__main__":
    async def main():
        await app.start()
        await logger.log(app, None, "Bot started", level="SUCCESS")
        print("Bot started...")
        await idle()
        print("\nStopping bot...")
        await logger.log(app, None, "Bot stopping", level="WARNING")
        global STOP_REQUESTED
        STOP_REQUESTED = True
        await app.stop()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
