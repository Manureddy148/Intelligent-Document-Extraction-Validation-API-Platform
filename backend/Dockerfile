FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

WORKDIR /srv/backend
ENV PYTHONPATH=/srv/backend

# Hosting platforms (Render, Railway, Koyeb, Fly) tell the container which port
# to serve on through $PORT; a container that ignores it fails its health check
# even though the application started correctly. Shell form so the variable is
# expanded at runtime, with a default for local use.
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
