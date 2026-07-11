import os
import sys
from datetime import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Settings:
    """Настройки приложения"""
    PROJECT_ROOT = Path(__file__).parent.parent
    PRICE_FILES_DIR = PROJECT_ROOT / "price_files"
    LOG_DIR = PROJECT_ROOT / "logs"

    @property
    def DATABASE_URL(self):
        """Строит connection string для MSSQL из настроек, или берёт из env"""
        url = os.getenv('DATABASE_URL', '')
        if url:
            return url
        if not self.MSSQL_SERVER:
            return 'sqlite:///./prices.db'  # fallback для локальной разработки
        from urllib.parse import quote_plus
        driver = "ODBC Driver 18 for SQL Server"
        trust = "yes" if self.MSSQL_TRUST_CERT == "yes" else "no"
        params = quote_plus(
            f"DRIVER={{{driver}}};SERVER={self.MSSQL_SERVER};"
            f"DATABASE={self.MSSQL_DATABASE};UID={self.MSSQL_USERNAME};"
            f"PWD={self.MSSQL_PASSWORD};TrustServerCertificate={trust}"
        )
        return f"mssql+pyodbc:///?odbc_connect={params}"

    BOT_TOKEN = os.getenv('BOT_TOKEN', '')
    DKC_LOGIN = os.getenv('DKC_LOGIN', 'branx')
    DKC_PASSWORD = os.getenv('DKC_PASSWORD', '11051987')
    PRICE_CHANGE_THRESHOLD = 0.01

    # MSSQL настройки
    MSSQL_SERVER = os.getenv('MSSQL_SERVER', '')
    MSSQL_DATABASE = os.getenv('MSSQL_DATABASE', 'Total_Price')
    MSSQL_USERNAME = os.getenv('MSSQL_USERNAME', 'sa')
    MSSQL_PASSWORD = os.getenv('MSSQL_PASSWORD', '')
    MSSQL_TRUST_CERT = os.getenv('MSSQL_TRUST_CERT', 'yes')

    # 1C-ERP интеграция
    ONE_C_LOGIN = os.getenv('ONE_C_LOGIN', '')
    ONE_C_PASSWORD = os.getenv('ONE_C_PASSWORD', '')
    ERP_BASE_URL = os.getenv('ERP_BASE_URL', '')

    # Типы номенклатуры из 1C, которые НЕ нужно заносить в БД.
    # Пример: ERP_SKIP_ITEM_TYPES=Товары на складах (41),Прочие материалы (10.06)
    @property
    def ERP_SKIP_ITEM_TYPES(self) -> set[str]:
        raw = os.getenv('ERP_SKIP_ITEM_TYPES', '')
        if not raw:
            return set()
        return {t.strip() for t in raw.split(',') if t.strip()}

    # Производители из 1C, которых НЕ нужно заносить в БД.
    # Пример: ERP_SKIP_MANUFACTURERS=ШИНА,RSVET,МЕКО,MEKO,ООО "ЕРС-КОМПЛЕКТ"
    @property
    def ERP_SKIP_MANUFACTURERS(self) -> set[str]:
        raw = os.getenv('ERP_SKIP_MANUFACTURERS', '')
        if not raw:
            return set()
        return {m.strip().lower() for m in raw.split(',') if m.strip()}

    # Доступ: список Telegram user ID через запятую
    @property
    def ADMIN_IDS(self) -> set[int]:
        raw = os.getenv('ADMIN_IDS', '')
        if not raw:
            return set()
        return {int(x.strip()) for x in raw.split(',') if x.strip().isdigit()}

    # --- Планировщик ---
    # Chat ID для уведомлений (узнать: отправь /start боту, посмотри в логах)
    NOTIFY_CHAT_ID = int(os.getenv('NOTIFY_CHAT_ID', '0')) or None

    # ERP: расписание (часы через запятую, например "8,13,18")
    @property
    def ERP_SYNC_TIMES(self) -> list[time]:
        raw = os.getenv('ERP_SYNC_TIMES', '')
        if not raw:
            return []
        times = []
        for part in raw.split(','):
            part = part.strip()
            if ':' in part:
                h, m = part.split(':')
                times.append(time(int(h), int(m)))
            elif part.isdigit():
                times.append(time(int(part), 0))
        return times

    # Sync all: день(и) недели (0=пн, 6=вс) и время
    # Примеры: "0" — только пн, "0,1,2,3,4,5,6" — каждый день
    @property
    def SYNC_ALL_DAYS(self) -> tuple[int, ...]:
        raw = os.getenv('SYNC_ALL_DAY', '0')
        return tuple(int(d.strip()) for d in raw.split(',') if d.strip().isdigit())

    @property
    def SYNC_ALL_TIME(self) -> time | None:
        raw = os.getenv('SYNC_ALL_TIME', '').strip().split(',')[0].strip()
        if not raw:
            return None
        if ':' in raw:
            h, m = raw.split(':', 1)
            return time(int(h), int(m))
        if raw.isdigit():
            return time(int(raw), 0)
        return None

    # --- Bitrix24 Bot ---
    # REST webhook URL (https://your.bitrix24.ru/rest/<user_id>/<token>/)
    BITRIX_REST_URL = os.getenv('BITRIX_REST_URL', '')
    # ID бота (из imbot.register или настроек)
    BITRIX_BOT_ID = int(os.getenv('BITRIX_BOT_ID', '0') or '0')
    # Порт FastAPI-сервера для приёма webhook от PHP-relay
    B24_BOT_PORT = int(os.getenv('B24_BOT_PORT', '7778') or '7778')
    # Токен для исходящих запросов Python→PHP bot_message_sender.php
    B24_WEBHOOK_TOKEN = os.getenv('B24_WEBHOOK_TOKEN', '') or os.getenv('FASTAPI_WEBHOOK_TOKEN', '')
    # ID группового чата для отправки алертов (например: chat22191)
    BITRIX_ALERT_CHAT_ID = os.getenv('BITRIX_ALERT_CHAT_ID', '')
    # PHP-эндпоинт для отправки сообщений от имени бота (обходит 403 у imbot.message.add)
    # Пример: http://192.168.10.12/local/ajax/bot_message_sender.php
    BITRIX_BOT_SENDER_URL = os.getenv('BITRIX_BOT_SENDER_URL', '')

    # Список Bitrix24 user ID, которым разрешено использовать бота
    @property
    def B24_ADMIN_IDS(self) -> set[int]:
        raw = os.getenv('B24_ADMIN_IDS', '')
        if not raw:
            return set()
        return {int(x.strip()) for x in raw.split(',') if x.strip().isdigit()}

    # Путь к Python из виртуального окружения
    @property
    def PYTHON_PATH(self):
        """Возвращает путь к Python интерпретатору"""
        if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            return sys.executable

        venv_python = self.PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)

        return 'python'

    def __init__(self):
        self.PRICE_FILES_DIR.mkdir(exist_ok=True, parents=True)
        self.LOG_DIR.mkdir(exist_ok=True, parents=True)

settings = Settings()
