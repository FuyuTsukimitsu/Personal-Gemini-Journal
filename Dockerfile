FROM python:3.12-slim

WORKDIR /app

# Install backend dependencies first for better layer caching.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend AND static frontend — both must be in the build context.
COPY backend/ ./backend/
COPY static/ ./static/

ENV PORT=8080
EXPOSE 8080

# UvicornWorker is required — gunicorn's default sync worker can't run
# FastAPI's async endpoints.
CMD exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT} --workers 1 --timeout 0 backend.main:app
