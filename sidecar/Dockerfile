FROM python:3.12-slim

WORKDIR /app
COPY main.py .

ENV OLLAMA_UPSTREAM=http://localhost:11435
ENV SIDECAR_PORT=11434

EXPOSE 11434

ENTRYPOINT ["python", "main.py"]
