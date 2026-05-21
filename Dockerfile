FROM python:3.14-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

COPY scripts/ ./scripts/

ENTRYPOINT ["python", "scripts/flickr_mcp.py"]
