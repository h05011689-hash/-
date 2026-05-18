FROM python:3.10-slim
WORKDIR /app
COPY main.py .
COPY detection.py .
RUN pip install python-telegram-bot groq pyrogram tgcrypto requests
CMD ["python", "main.py"]
