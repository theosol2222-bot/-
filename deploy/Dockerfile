FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-inter fonts-dejavu-core ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 clip

WORKDIR /app
COPY pyproject.toml ./
COPY clipfactory ./clipfactory
RUN pip install --no-cache-dir .
COPY config ./config

USER clip
ENV CLIPFACTORY_SETTINGS=/app/config/settings.yaml \
    CLIPFACTORY_STREAMERS=/app/config/streamers.yaml \
    HF_HOME=/app/data/models/hf
VOLUME ["/app/data"]
EXPOSE 8080
# Moniteur 24/7 + tableau de bord dans un seul processus
CMD ["python", "-m", "clipfactory", "run", "--host", "0.0.0.0", "--port", "8080"]
