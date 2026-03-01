# Use Alpine as base image
FROM python:3.12-alpine

# Install ffmpeg and dependencies
RUN apk add --no-cache ffmpeg

# Set working directory
WORKDIR /app

# Copy only necessary files
COPY requirements.txt bot.py /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the bot
CMD ["python", "bot.py"]