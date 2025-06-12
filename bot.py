import os
import logging
import time
import asyncio
import socket
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TimedOut, NetworkError
import yt_dlp
from yt_dlp.utils import DownloadError
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
        self.update_interval = 5  # Update status every 5 seconds to reduce API calls

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
    
    # Set a reasonable timeout for the entire operation
    download_timeout = 300  # 5 minutes total timeout
    
    try:
        # Create a temporary directory to store the downloaded file
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set operation start time for timeout tracking
            operation_start_time = time.time()
            # Create progress hook
            progress_handler = DownloadProgressHook(status_message)
            
            # Set default socket timeout
            socket.setdefaulttimeout(30)  # 30 seconds timeout for all socket operations
            
            # Basic options without format specification
            ydl_opts = {
                'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 20,  # Socket timeout in seconds
                'retries': 3,  # Retry up to 3 times
                'fragment_retries': 3,  # Retry fragments up to 3 times
                'extractor_retries': 3,  # Retry extractor up to 3 times
                'progress_hooks': [lambda d: asyncio.run_coroutine_threadsafe(
                    progress_handler.progress_hook(d), 
                    asyncio.get_event_loop()
                )]
            }
            
            # Special handling for YouTube
            if 'youtube.com' in url or 'youtu.be' in url:
                await status_message.edit_text("⏳ YouTube video detected. Getting available formats...")
                
                # For YouTube, don't specify format initially - just get the formats
                with yt_dlp.YoutubeDL({**ydl_opts, 'quiet': True, 'listformats': True}) as ydl:
                    # Just get the info without any format filtering
                    info = ydl.extract_info(url, download=False)
                
                # Use the most basic format option for YouTube
                ydl_opts["format"] = "18/17/13"  # Standard formats that almost always exist (360p/240p/144p)
            else:
                # For non-YouTube sites, use the normal approach
                with yt_dlp.YoutubeDL({**ydl_opts, 'quiet': True}) as ydl:
                    await status_message.edit_text("⏳ Getting video information...")
                    info = ydl.extract_info(url, download=False)
                    formats = info.get("formats", [])
                    
                    if not formats:
                        await status_message.edit_text("❌ No available formats for this video.")
                        return
                    
                    # For other sites, try to select the best format under the size limit
                    selected_format = None
                    for fmt in formats:
                        if "filesize" in fmt and fmt["filesize"] is not None and fmt["filesize"] < MAX_TELEGRAM_FILE_SIZE:
                            selected_format = fmt["format_id"]
                            break
                    
                    if not selected_format:
                        await status_message.edit_text("⚠️ Video too large. Trying lowest quality...")
                        selected_format = "worst"
                    
                    ydl_opts["format"] = selected_format
            
            # Add timeout handling
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Check if we've exceeded our total operation timeout
                    if time.time() - operation_start_time > download_timeout:
                        await status_message.edit_text("⚠️ Operation timed out. Trying to process what we have...")
                    else:
                        ydl.download([url])
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                logging.error(f"Download error: {error_msg}")
                
                # For YouTube, try a sequence of known format IDs
                if ('youtube.com' in url or 'youtu.be' in url) and "format" in error_msg.lower():
                    await status_message.edit_text("⚠️ YouTube format issue. Trying alternative formats...")
                    
                    # Try a sequence of common YouTube format IDs
                    youtube_formats = ["18", "22", "17", "13"]  # 360p, 720p, 144p, etc.
                    success = False
                    
                    for fmt in youtube_formats:
                        try:
                            simple_opts = dict(ydl_opts)
                            simple_opts["format"] = fmt
                            with yt_dlp.YoutubeDL(simple_opts) as ydl:
                                ydl.download([url])
                            success = True
                            break
                        except Exception as fmt_error:
                            logging.error(f"Format {fmt} failed: {str(fmt_error)}")
                            continue
                    
                    if not success:
                        await status_message.edit_text("❌ Could not download any format of this YouTube video.")
                        return
                
                # For non-YouTube or if YouTube specific handling failed
                elif "Requested format is not available" in error_msg or "format not available" in error_msg.lower():
                    await status_message.edit_text("⚠️ Format issue detected. Trying with basic settings...")
                    try:
                        # Last resort: use the most basic format option
                        simple_opts = dict(ydl_opts)
                        simple_opts["format"] = "worst"
                        with yt_dlp.YoutubeDL(simple_opts) as ydl:
                            ydl.download([url])
                    except Exception as retry_error:
                        await status_message.edit_text(f"❌ Failed to download video: {str(retry_error)}")
                        return
                else:
                    await status_message.edit_text(f"❌ Download error: {error_msg}")
                    return
            except socket.timeout:
                await status_message.edit_text("⚠️ Network timeout occurred. Trying to process what we have...")
            
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
                # For larger files, use a much longer timeout
                file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
                upload_timeout = max(300, int(file_size_mb * 10))  # 10 seconds per MB, minimum 5 minutes
                
                await status_message.edit_text(f"✅ Uploading video ({file_size_mb:.1f}MB)...")
                
                # Open and send the file with a longer timeout
                with open(video_path, 'rb') as video_file:
                    await asyncio.wait_for(
                        update.message.reply_video(
                            video=video_file,
                            caption=f"📹 {info.get('title', 'Downloaded Video')}",
                            supports_streaming=True
                        ),
                        timeout=upload_timeout
                    )
                
                await status_message.delete()
            except asyncio.TimeoutError:
                logging.error("Timeout while sending video to Telegram")
                await status_message.edit_text("⚠️ Video downloaded but sending timed out. Trying again with a longer timeout...")
                
                # Try again with an even longer timeout
                try:
                    with open(video_path, 'rb') as video_file:
                        # Double the timeout for the retry
                        await asyncio.wait_for(
                            update.message.reply_video(
                                video=video_file,
                                caption=f"📹 {info.get('title', 'Downloaded Video')} (retry)",
                                supports_streaming=True
                            ),
                            timeout=upload_timeout * 2  # Double the original timeout
                        )
                    await status_message.delete()
                except Exception as retry_error:
                    logging.error(f"Retry also failed: {retry_error}")
                    await status_message.edit_text("⚠️ Video downloaded but couldn't be sent. The file might be too large for your connection.")
            
            except (TimedOut, NetworkError) as telegram_error:
                logging.error(f"Telegram API error: {telegram_error}")
                await status_message.edit_text("⚠️ Network issue while sending video. Trying again...")
                
                # Wait a moment before retrying
                await asyncio.sleep(2)
                
                # Try again with a longer timeout
                try:
                    with open(video_path, 'rb') as video_file:
                        await asyncio.wait_for(
                            update.message.reply_video(
                                video=video_file,
                                caption=f"📹 {info.get('title', 'Downloaded Video')} (retry)",
                                supports_streaming=True
                            ),
                            timeout=upload_timeout * 2  # Double the original timeout
                        )
                    await status_message.delete()
                except Exception as retry_error:
                    logging.error(f"Retry also failed: {retry_error}")
                    await status_message.edit_text("⚠️ Video downloaded but couldn't be sent due to network issues.")
            except Exception as send_error:
                logging.error(f"Error sending video: {send_error}")
                await status_message.edit_text(f"⚠️ Video downloaded but couldn't send: {str(send_error)}")
            
    except asyncio.TimeoutError:
        logging.error("Asyncio timeout error")
        await status_message.edit_text("⚠️ Operation timed out. Please try again with a different video.")
    except socket.timeout:
        logging.error("Socket timeout error")
        await status_message.edit_text("⚠️ Network connection timed out. Please try again later.")
    except Exception as e:
        logging.error(f"Error downloading video: {e}")
        await status_message.edit_text(f"❌ Error: {str(e)}")

def main() -> None:
    """Start the bot."""
    # Configure application with appropriate timeouts
    application = Application.builder().token(TOKEN).connect_timeout(30.0).pool_timeout(30.0).build()
    
    # Set connection pool timeouts
    application.http_version = "1.1"  # Use HTTP 1.1 which has better timeout handling
    application.connection_pool_size = 8  # Increase connection pool for better handling
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    
    # Start the bot with appropriate polling parameters
    application.run_polling(timeout=30)

if __name__ == "__main__":
    main()
