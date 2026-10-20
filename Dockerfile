# Vyuh container: builds data + models on first start, then serves the app on port 8000.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Create the folders that docker-compose mounts as volumes BEFORE switching user.
# Docker copies a folder's owner into a new (empty) named volume, so the app user
# can write there. Without this the volumes are owned by root and the app user gets
# "Permission denied: /app/data/events.csv".
RUN mkdir -p data models/saved reports \
 && useradd --create-home vyuh \
 && chown -R vyuh:vyuh /app
USER vyuh
EXPOSE 8000
# Prepare data + models on first start only; stop with a clear error if that fails.
CMD ["sh", "-c", "if [ ! -f models/saved/journey.txt ]; then python -m scripts.prepare --skip-adversarial || exit 1; fi; exec uvicorn api.main:app --host 0.0.0.0 --port 8000"]
