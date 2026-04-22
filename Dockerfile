FROM python:3.11-slim-bookworm

WORKDIR /app

ARG PROXY_URL=""

# Системные зависимости + ODBC Driver 18 for SQL Server
RUN if [ -n "$PROXY_URL" ]; then \
        PROXY_APT=$(echo "$PROXY_URL" | sed 's|^socks5://|socks5h://|'); \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$PROXY_APT" "$PROXY_APT" \
            > /etc/apt/apt.conf.d/01proxy; \
    fi \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gcc \
    curl \
    gnupg2 \
    apt-transport-https \
    ca-certificates \
    unixodbc-dev \
    python3-socks \
    && python -c "import site, shutil; shutil.copy('/usr/lib/python3/dist-packages/socks.py', site.getsitepackages()[0])" \
    && ALL_PROXY="$(echo "$PROXY_URL" | sed 's|^socks5://|socks5h://|')" curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o /tmp/microsoft.asc \
    && gpg --batch --yes --dearmor -o /usr/share/keyrings/microsoft-prod.gpg /tmp/microsoft.asc \
    && rm /tmp/microsoft.asc \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg2 apt-transport-https \
    && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy

# Зависимости Python
COPY requirements.txt .
RUN PROXY_H=$(echo "$PROXY_URL" | sed 's|^socks5://|socks5h://|') \
    && if [ -n "$PROXY_H" ]; then \
        pip install --no-cache-dir --proxy "$PROXY_H" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

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
