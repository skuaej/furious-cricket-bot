# Use an official, lightweight Python runtime
FROM python:3.10-slim

# Prevent Python from writing .pyc files and force unbuffered logging (critical for container logs)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies if your bot or image generation tools require them (optional)
# RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements first to efficiently leverage Docker's build cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application files
COPY . .

# Run the bot directly using the executable form to ensure it handles termination signals cleanly
CMD ["python", "main.py"]
