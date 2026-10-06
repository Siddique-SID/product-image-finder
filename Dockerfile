FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY config.py product_image_finder.py ./
COPY backend/ ./backend/
COPY --from=frontend /build/frontend/dist ./frontend/dist
ENV DATA_DIR=/data/pwa
RUN mkdir -p /data/pwa && useradd --uid 10001 --create-home appuser && chown -R appuser:appuser /data /app
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "python -m uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
