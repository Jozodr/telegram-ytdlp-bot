import os
import logging
import time
import asyncio
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

class DownloadProgressHook:
    def __init__(self, status_message):
        self.status_message = status_message
        self.start_time = time.time()
        self.last_update_time = 0
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.filename = ""
        self.update_interval = 3  # Update status every 3 seconds

    async def progress_hook(self, d):
        if d['status'] == 'downloading':
            self.downloaded_bytes = d.get('downloaded_bytes', 0)
            self.total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            self.filename = d.get('filename', '').split('/')[-1]
            
            # Update status message every few seconds to avoid API rate limits
            current_time = time.time()
            if current_time - self.last_update_time > self.update_interval:
                self.last_update_time = current_time
                await self.update_status()
        
        elif d['status'] == 'finished':
            await self.status_message.edit_text(f"✅ Download complete! Processing video...")

    async def update_status(self):
        elapsed_time = time.time() - self.start_time
        
        if self.total_bytes > 0:
            percent = self.downloaded_bytes * 100 / self.total_bytes
            progress_bar = self.get_progress_bar(percent)
            size_mb = self.total_bytes / (1024 * 1024)
            downloaded_mb = self.downloaded_bytes / (1024 * 1024)
            
            status_text = (f"⏳ Downloading: {progress_bar} {percent:.1f}%\n"
                          f"📦 {downloaded_mb:.1f}MB / {size_mb:.1f}MB\n"
                          f"⏱️ {int(elapsed_time)}s elapsed")
        else:
            status_text = f"⏳ Downloading... ({int(elapsed_time)}s elapsed)"
            
        try:
            await self.status_message.edit_text(status_text)
        except Exception as e:
            logging.error(f"Failed to update status: {e}")
    
    def get_progress_bar(self, percent, length=10):
        filled_length = int(length * percent // 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return bar

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download video from the provided URL and send it back."""
    url = update.message.text
    
    # Check if the message looks like a URL
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("Please send a valid video URL.")
        return
        
    # Handle Instagram Threads URLs by converting them to standard Instagram URLs
    if 'threads.net' in url:
        # Convert Threads URL to Instagram URL format
        url = url.replace('threads.net', 'instagram.com')
    
    status_message = await update.message.reply_text("⏳ Analyzing video URL... Please wait.")
    
    try:
        # Create a temporary directory to store the downloaded file
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create progress hook
            progress_handler = DownloadProgressHook(status_message)
            
            ydl_opts = {
                'format': 'mp4',  # Use a single container format that doesn't require merging
                'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [lambda d: asyncio.run_coroutine_threadsafe(
                    progress_handler.progress_hook(d), 
                    asyncio.get_event_loop()
                )]
            }
            
            # First get video info without downloading
            with yt_dlp.YoutubeDL({**ydl_opts, 'quiet': True}) as ydl:
                await status_message.edit_text("⏳ Getting video information...")
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

            # Now download with progress updates
            ydl_opts["format"] = selected_format
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
            
            try:
                with open(video_path, 'rb') as video_file:
                    await update.message.reply_video(
                        video=video_file,
                        caption=f"📹 {info.get('title', 'Downloaded Video')}",
                        supports_streaming=True
                    )
                
                await status_message.delete()
            except Exception as send_error:
                logging.error(f"Error sending video: {send_error}")
                await status_message.edit_text(f"⚠️ Video downloaded but couldn't send: {str(send_error)}")
            
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
