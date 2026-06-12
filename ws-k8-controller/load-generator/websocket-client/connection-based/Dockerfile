FROM python:3.11-slim

WORKDIR /app

COPY client.py .

RUN pip install websockets

CMD ["python", "client.py"]
