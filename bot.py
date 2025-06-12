import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import tempfile

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Get the token from environment variables for security
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")

# Maximum file size for Telegram (50MB)
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text("Hi! Send me a video URL, and I'll download it for you.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("Send a URL from YouTube, Twitter, Instagram, etc., and I'll download it.")

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download video from the provided URL and send it back."""
    url = update.message.text
    
    # Check if the message looks like a URL
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("Please send a valid video URL.")
        return
    
    status_message = await update.message.reply_text("⏳ Downloading video... Please wait.")
    
    try:
        # Create a temporary directory to store the downloaded file
        with tempfile.TemporaryDirectory() as temp_dir:
            ydl_opts = {
                'format': 'mp4',  # Use a single container format that doesn't require merging
                'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get("formats", [])
                
                if not formats:
                    await status_message.edit_text("❌ No available formats for this video.")
                    return

                # Try to get the best quality within Telegram's limit
                selected_format = None
                for fmt in formats:
                    if "filesize" in fmt and fmt["filesize"] is not None and fmt["filesize"] < MAX_TELEGRAM_FILE_SIZE:
                        selected_format = fmt["format_id"]
                        break
                
                if not selected_format:
                    await status_message.edit_text("⚠️ Video too large. Trying lowest quality...")
                    selected_format = "worst"

                ydl_opts["format"] = selected_format  # Use the selected format
                ydl.download([url])
            
            # Find the downloaded file
            files = os.listdir(temp_dir)
            if not files:
                await status_message.edit_text("❌ Failed to download the video.")
                return
            
            video_path = os.path.join(temp_dir, files[0])
            
            # Check file size
            file_size = os.path.getsize(video_path)
            if file_size > MAX_TELEGRAM_FILE_SIZE:
                await status_message.edit_text("❌ Video is still too large for Telegram (>50MB).")
                return
            
            # Send the video
            await status_message.edit_text("✅ Download complete! Sending video...")
            
            with open(video_path, 'rb') as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=f"📹 {info.get('title', 'Downloaded Video')}",
                    supports_streaming=True
                )
            
            await status_message.delete()
            
    except Exception as e:
        logging.error(f"Error downloading video: {e}")
        await status_message.edit_text(f"❌ Error: {str(e)}")

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    application.run_polling()

if __name__ == "__main__":
    main()
