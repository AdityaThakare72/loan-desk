# Small official Python image
FROM python:3.12-slim

WORKDIR /app

# 1. Dependencies first: this layer is cached and rebuilt only when requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Then our code and data (changes often, rebuilds in seconds)
COPY app/ app/
COPY data/ data/
COPY policies/ policies/

# Run as a normal user, not root
RUN useradd --create-home appuser && mkdir -p /app/logs && chown appuser /app/logs
USER appuser

EXPOSE 8000
# Secrets are NOT in the image: they come from --env-file at "docker run"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
