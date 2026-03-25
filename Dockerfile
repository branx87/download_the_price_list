FROM python:3.11-slim

WORKDIR /app

# Системные зависимости + ODBC Driver 18 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    gnupg2 \
    apt-transport-https \
    unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
       msodbcsql18 \
       libgssapi-krb5-2 \
       libkrb5-3 \
       libk5crypto3 \
    && ldd /opt/microsoft/msodbcsql18/lib64/libmsodbcsql-*.so* \
    && apt-get purge -y --auto-remove curl gnupg2 apt-transport-https \
    && rm -rf /var/lib/apt/lists/*

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY . .

# Создаём директории для данных
RUN mkdir -p logs price_files reports

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Europe/Moscow

# Healthcheck: проверяем что процесс бота жив
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os; pid=open('logs/bot.pid').read().strip(); os.kill(int(pid), 0)" || exit 1

ENTRYPOINT ["python", "-m", "bot.main"]
