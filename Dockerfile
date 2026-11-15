FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt python-dotenv
COPY . .
EXPOSE 8080
CMD ["python", "run.py"]
