FROM ollama/ollama:rocm

# AMD GC_11_5_0 (k8s-node-max-01) is not natively recognized — bake the override
# in so the image works without requiring the ConfigMap workaround
ENV HSA_OVERRIDE_GFX_VERSION=11.5.1

# Harbor as primary registry; falls back to registry.ollama.ai
# Requires Harbor project ai/ to be configured for Ollama OCI artifacts (issue #15)
ENV OLLAMA_REGISTRIES=harbor.internal/ai,registry.ollama.ai

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY warmup.sh /usr/local/bin/warmup.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/warmup.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
