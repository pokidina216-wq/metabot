FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libmagic1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    aiogram python-dotenv Pillow mutagen pypdf \
    python-docx openpyxl python-pptx aiohttp aiohttp-socks

COPY bot_lite.py .
COPY proxies.txt .
COPY welcome.png .

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot_lite.py"]
