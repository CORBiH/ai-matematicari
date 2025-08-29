FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Shell forma: bash ekspanduje $PORT prije pokretanja gunicorna
CMD ["bash","-lc","exec gunicorn -b 0.0.0.0:$PORT -w 2 -k gthread --threads 8 --timeout 120 --graceful-timeout 90 app:app"]
