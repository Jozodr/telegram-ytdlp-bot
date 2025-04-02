# Use an official lightweight Python image
FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Copy only necessary files to the container
COPY requirements.txt bot.py /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables (Optional: Use Docker secrets for security)
ENV TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN_HERE"

# Run the bot
CMD ["python", "bot.py"]