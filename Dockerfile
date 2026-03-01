# Use Alpine as base image
FROM python:3.12-alpine

# Install ffmpeg and dependencies
RUN apk add --no-cache ffmpeg

# Set working directory
WORKDIR /app

# Build metadata (set by CI)
ARG BUILD_VERSION=unspecified
ARG VCS_REF=unspecified

LABEL org.opencontainers.image.version="${BUILD_VERSION}" \
	org.opencontainers.image.revision="${VCS_REF}" \
	org.opencontainers.image.source="https://github.com/Jozodr/telegram-ytdlp-bot"

# Copy only necessary files
COPY requirements.txt bot.py /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the bot
CMD ["python", "bot.py"]