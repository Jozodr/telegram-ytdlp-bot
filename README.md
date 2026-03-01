# Telegram YT-DLP Bot

A simple Telegram bot that downloads videos using yt-dlp and sends them to users.

## Features

- Download videos from YouTube and other supported platforms
- Send downloaded videos directly to Telegram
- Lightweight Alpine-based Docker image
- Multi-architecture support (amd64, arm64)

## Requirements

- Telegram Bot Token (get it from [@BotFather](https://t.me/botfather))
- Docker (optional, for containerized deployment)

## Installation

### Local Setup

1. Clone the repository:
```bash
git clone https://github.com/Jozodr/telegram-ytdlp-bot.git
cd telegram-ytdlp-bot
```

2. Create virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the bot:
```bash
export TELEGRAM_BOT_TOKEN="your_token_here"
python bot.py
```

### Docker Setup

1. Build the image:
```bash
docker build -t telegram-ytdlp-bot .
```

2. Run the container:
```bash
docker run -d \
  --name telegram-ytdlp-bot \
  -e TELEGRAM_BOT_TOKEN="your_token_here" \
  telegram-ytdlp-bot
```

### Docker Pull from GHCR

```bash
docker pull ghcr.io/jozodr/telegram-ytdlp-bot:latest
docker run -d \
  --name telegram-ytdlp-bot \
  -e TELEGRAM_BOT_TOKEN="your_token_here" \
  ghcr.io/jozodr/telegram-ytdlp-bot:latest
```

## Configuration

Set the following environment variable:

- `TELEGRAM_BOT_TOKEN`: Your Telegram Bot Token

## Usage

Send a video URL to the bot and it will download and send the video directly to you.

Supported platforms: YouTube, Instagram, TikTok, and many more (see [yt-dlp](https://github.com/yt-dlp/yt-dlp) for full list)

## Development

The bot uses:
- [python-telegram-bot](https://python-telegram-bot.readthedocs.io/) - Telegram Bot API
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Video downloading

## License

MIT

## Support

For issues or questions, please open an issue on GitHub.
