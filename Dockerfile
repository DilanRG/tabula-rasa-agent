FROM python:3.12-slim

WORKDIR /app

# Install git for self-modification
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN useradd -m agent
USER agent

# Copy source code (will be mounted in dev, but for production it's here)
COPY agent/ ./agent/
COPY config.yaml .

# Set entrypoint
CMD ["python", "-m", "agent.core"]
