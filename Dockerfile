FROM node:20-slim AS frontend

WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ .
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

# ffmpeg for piper WAV -> MP3 conversion; build-essential for any
# native deps that have to compile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch Piper voices so first synth doesn't have to download.
# Bryce Beattie's voices live outside the upstream piper-tts manifest,
# so we fetch them directly. Tracked by issue #20.
RUN mkdir -p /app/data/piper_models /app/data/dj_audio /app/data/exports && \
    python -c "import urllib.request as u; \
voices = ['cori-high', 'kristin', 'bryce', 'norman', 'mv2', 'jenny']; \
[u.urlretrieve(f'https://sfo3.digitaloceanspaces.com/bkmdls/{v}.{ext}', f'/app/data/piper_models/{v}.{ext}') for v in voices for ext in ('onnx','onnx.json')]; \
print(f'fetched {len(voices)} voices')"
RUN cd /app/data/piper_models && python -m piper.download_voices en_US-lessac-medium

COPY rose_cinema/ rose_cinema/
COPY alembic/ alembic/
COPY alembic.ini .
COPY agents/ agents/
COPY --from=frontend /build/dist/ web/dist/

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn rose_cinema.api:app --host 0.0.0.0 --port 8000"]
