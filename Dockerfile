FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY scripts/ ./scripts/
COPY bin/ ./bin/

ENTRYPOINT ["python", "scripts/flickr.py"]
CMD ["--help"]
