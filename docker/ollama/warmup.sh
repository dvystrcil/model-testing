#!/bin/bash
set -euo pipefail

# Models to pre-load, comma-separated.
# Override via OLLAMA_WARMUP_MODELS env var in the K8s deployment.
MODELS="${OLLAMA_WARMUP_MODELS:-qwen3:0.6b,qwen3-coder-next:latest}"

IFS=',' read -ra MODEL_LIST <<< "$MODELS"
for model in "${MODEL_LIST[@]}"; do
    model="${model// /}"  # trim whitespace
    if [ -z "$model" ]; then continue; fi

    echo "Pulling: $model"
    ollama pull "$model" || {
        echo "ERROR: failed to pull $model" >&2
        exit 1
    }

    echo "Warming up: $model"
    # Empty prompt — forces model into VRAM without generating meaningful output
    echo "" | ollama run "$model" --nowordwrap 2>/dev/null || true
    echo "Loaded: $model"
done

echo "Warmup complete: ${MODEL_LIST[*]}"
