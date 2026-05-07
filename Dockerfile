FROM node:20-slim AS frontend

WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ .
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

# ffmpeg for piper WAV -> MP3 conversion and icecast encoding;
# icecast2 for stream serving; build-essential for any native deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    icecast2 \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# squeezelite: Debian's package (2.0.0-1517) is too old for MA 2.8+'s
# SlimProto protocol (unhandled "vers" command). Fetch a recent static build.
RUN curl -sL 'https://sourceforge.net/projects/lmsclients/files/squeezelite/linux/squeezelite-2.0.0.1541-x86_64.tar.gz/download' \
    | tar xz -C /usr/local/bin squeezelite && chmod +x /usr/local/bin/squeezelite

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch Piper voices so first synth doesn't have to download.
# Bryce Beattie's voices live outside the upstream piper-tts manifest,
# so we fetch them directly. Tracked by issue #20.
RUN mkdir -p /app/data/piper_models /app/data/dj_audio /app/data/exports /app/data/album_art /app/data/tracks /app/data/streams && \
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
COPY deploy/icecast.xml /etc/icecast2/icecast.xml
RUN mkdir -p /var/log/icecast2 && chown nobody:nogroup /var/log/icecast2

EXPOSE 8000

CMD ["sh", "-c", "icecast2 -c /etc/icecast2/icecast.xml -b && alembic upgrade head && exec python -m uvicorn rose_cinema.api:app --host 0.0.0.0 --port 8000"]
