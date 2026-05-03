#!/bin/bash
set -euo pipefail

# Start Ollama server in the background
ollama serve &
OLLAMA_PID=$!

# Wait for API to accept connections (up to 60s)
echo "Waiting for Ollama API..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        echo "Ollama ready (${i}s)"
        break
    fi
    sleep 1
done

# Pre-load required models into VRAM
/usr/local/bin/warmup.sh

# Signal readiness — Kubernetes readiness probe checks for this file
touch /tmp/models-ready
echo "All models loaded. Pod is ready."

# Hand off to Ollama process
wait "$OLLAMA_PID"
