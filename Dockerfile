FROM python:3.14-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m -u 1000 app && \
    chown -R app /app && \
    mkdir -p /home/app/.flickr_mcp && \
    chown app /home/app/.flickr_mcp

COPY --chown=app scripts/ ./scripts/

USER app

EXPOSE 8000

ENTRYPOINT ["python", "scripts/flickr_mcp.py"]
