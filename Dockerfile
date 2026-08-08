
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
ARG CACHEBUST=0
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m -u 1000 app && \
    chown -R app /app && \
    mkdir -p /home/app/.flickr_mcp && \
    chown app /home/app/.flickr_mcp && \
    mkdir -p /app/data && \
    chown app /app/data

ARG CACHEBUST=0

COPY --chown=app scripts/ ./scripts/
COPY --chown=app templates/ ./templates/
COPY --chown=app static/ ./static/
COPY --chown=app default-prompts.md ./default-prompts.md
COPY --chown=app --from=frontend /build/dist ./frontend/dist

USER app

EXPOSE 8000

ENTRYPOINT ["python", "scripts/flickr_mcp.py"]
