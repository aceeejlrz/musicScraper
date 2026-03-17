FROM python:3.10-slim

# Install ffmpeg & aria2
RUN apt-get update && \
    apt-get install -y ffmpeg aria2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose the app port
EXPOSE 5050

# Run the backend
CMD ["python", "app.py"]
