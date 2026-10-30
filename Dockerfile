FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE_PATH=/app/data/mim_reklam_satis_sistemi.db UPLOAD_FOLDER=/app/uploaded_files
RUN groupadd -g 1000 flask && useradd -u 1000 -g flask -m flask
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN mkdir -p /app/data /app/uploaded_files && chown -R flask:flask /app
USER flask
EXPOSE 8000
CMD ["sh", "-c", "python -c 'from app import init_system; init_system()' && exec gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 app:app"]
