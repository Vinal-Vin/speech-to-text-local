FROM python:3.12-slim

# libsndfile is the native dependency behind soundfile (audio decoding).
# curl is used by the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Optional backends are opt-in: together they add >2 GB (torch, CTranslate2),
# and the server lazy-imports backends, so leaving them out costs nothing
# until one is actually selected via ASR_BACKEND.
ARG INSTALL_WHISPER=0
ARG INSTALL_QWEN=0

COPY requirements.txt requirements-whisper.txt requirements-qwen.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_WHISPER" = "1" ]; then pip install --no-cache-dir -r requirements-whisper.txt; fi \
    && if [ "$INSTALL_QWEN" = "1" ]; then pip install --no-cache-dir -r requirements-qwen.txt; fi

COPY app ./app

# Model weights live on a mounted volume so they survive image rebuilds --
# without this, every rebuild re-downloads gigabytes.
ENV HF_HOME=/models \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
