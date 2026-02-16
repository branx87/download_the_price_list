import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Settings:
    """Настройки приложения"""
    PROJECT_ROOT = Path(__file__).parent.parent
    PRICE_FILES_DIR = PROJECT_ROOT / "price_files"
    LOG_DIR = PROJECT_ROOT / "logs"

    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./prices.db')
    BOT_TOKEN = os.getenv('BOT_TOKEN', '')
    DKC_LOGIN = os.getenv('DKC_LOGIN', 'branx')
    DKC_PASSWORD = os.getenv('DKC_PASSWORD', '11051987')
    PRICE_CHANGE_THRESHOLD = 0.01

    # 1C-ERP интеграция
    ONE_C_LOGIN = os.getenv('ONE_C_LOGIN', '')
    ONE_C_PASSWORD = os.getenv('ONE_C_PASSWORD', '')
    ERP_BASE_URL = os.getenv('ERP_BASE_URL', '')

    # Путь к Python из виртуального окружения
    @property
    def PYTHON_PATH(self):
        """Возвращает путь к Python интерпретатору"""
        # Если запущен из venv, используем текущий интерпретатор
        if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            return sys.executable

        # Иначе пробуем найти venv в PROJECT_ROOT
        venv_python = self.PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)

        # Fallback на системный python
        return 'python'

    def __init__(self):
        self.PRICE_FILES_DIR.mkdir(exist_ok=True, parents=True)
        self.LOG_DIR.mkdir(exist_ok=True, parents=True)

settings = Settings()
