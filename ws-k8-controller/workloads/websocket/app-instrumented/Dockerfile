FROM python:3.11-slim

WORKDIR /app

COPY server.py .

RUN pip install websockets aiohttp prometheus_client

EXPOSE 8765
EXPOSE 8080

CMD ["python", "server.py"]