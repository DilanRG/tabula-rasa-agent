FROM python:3.12-slim

WORKDIR /app

# Install git for self-modification
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN useradd -m agent
USER agent

RUN git config --global user.name "Tabula Rasa Agent" && \
    git config --global user.email "agent@tabula-rasa.local" && \
    git config --global safe.directory /app

# Copy source code (will be mounted in dev, but for production it's here)
COPY agent/ ./agent/
COPY config.yaml .

# Set entrypoint
CMD ["python", "-m", "agent.core"]
