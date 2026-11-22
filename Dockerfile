# ===========================================================================
# NSO Diary API — контейнер для Hugging Face Spaces / любого Docker-хостинга
#
# Секреты НЕ копируются в образ: файлы .env* исключены в .dockerignore.
# Их нужно задать в настройках хостинга (переменные окружения / Secrets).
# ===========================================================================

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

# Сначала зависимости — так слой кэшируется и пересборка быстрее.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Непривилегированный пользователь: у HF Spaces контейнер запускается от root,
# но приложение от этого не зависит.
EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/api/health', timeout=8)"

CMD ["python", "main.py"]
