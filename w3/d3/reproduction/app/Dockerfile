FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir prometheus_client
COPY main.py .
EXPOSE 8080
CMD ["python", "main.py"]
