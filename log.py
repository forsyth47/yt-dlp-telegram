import datetime
import config
from pyrogram import Client, types

LOG_FILE = "log.txt"

async def log(app: Client, message: types.Message = None, text: str = "", level: str = "INFO"):
    """
    Central logging function.
    Triggers local file logging and Telegram channel logging.

    :param app: Pyrogram Client instance
    :param message: Pyrogram Message object (optional context)
    :param text: The log message
    :param level: Log level (INFO, ERROR, WARNING, SUCCESS)
    """
    log_local(message, text, level)
    await log_telegram(app, message, text, level)

def log_local(message, text, level):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level}] {text}"

    if message:
        try:
            user_id = message.from_user.id if message.from_user else "Unknown"
            username = message.from_user.username if message.from_user else "Unknown"
            chat_id = message.chat.id if message.chat else "Unknown"
            entry += f" | User: {username} ({user_id}) | Chat: {chat_id}"
        except Exception:
            pass

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"Local log error: {e}")

async def log_telegram(app: Client, message: types.Message, text: str, level: str):
    logs_id = getattr(config, 'logs_id', None)
    if not logs_id:
        # print("DEBUG: No logs_id found in config")
        return

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Emoji for levels
    emoji = "ℹ️"
    if level == "WARNING": emoji = "⚠️"
    elif level == "SUCCESS": emoji = "✅"
    elif level == "ERROR": emoji = "❌"

    msg = f"{emoji} **{level}**\n`{timestamp}`\n\n{text}"

    if message:
        try:
            user = message.from_user
            chat = message.chat

            if user:
                msg += f"\n\n👤 **User:** {user.mention} (`{user.id}`)"
            if chat:
                title = chat.title or chat.first_name or "Unknown"
                msg += f"\n📢 **Chat:** {title} (`{chat.id}`)"
        except Exception:
            pass

    try:
        print(f"DEBUG: Attempting to send log to {logs_id}")
        await app.send_message(logs_id, msg, disable_web_page_preview=True)
    except Exception as e:
        print(f"Telegram log error: {e} (Chat ID: {logs_id})")