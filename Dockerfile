FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY ks/ ks/
COPY db/postgres/ db/postgres/

USER 10001:10001
CMD ["python", "-m", "ks.mcp_server", "--transport", "streamable-http", "--host", "0.0.0.0"]
