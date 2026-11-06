FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sentinel_backend sentinel_backend
USER 65534
EXPOSE 8000
CMD ["uvicorn", "sentinel_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
