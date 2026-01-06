import os
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

    def __init__(self):
        self.PRICE_FILES_DIR.mkdir(exist_ok=True, parents=True)
        self.LOG_DIR.mkdir(exist_ok=True, parents=True)

settings = Settings()
