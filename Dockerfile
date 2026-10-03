FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create essential directories
RUN mkdir -p /app/downloads /app/output /app/gallery-dl

ENV PYTHONUNBUFFERED=1

# Expose HTTP tunnel / direct file download port
EXPOSE 8080

CMD ["python", "main.py", "--bot"]
