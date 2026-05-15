FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir requests mcp starlette "uvicorn[standard]"

EXPOSE 8000

COPY scripts/ ./scripts/
COPY bin/ ./bin/

ENTRYPOINT ["python", "scripts/flickr_mcp.py"]
