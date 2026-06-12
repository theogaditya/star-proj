FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY broker.py .
EXPOSE 1883 8080
CMD ["python", "broker.py"]
