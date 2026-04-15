import logging
from pathlib import Path

from domain.interfaces.downloader import IDownloader

logger = logging.getLogger(__name__)


class UploadDownloader(IDownloader):
    """Downloader-заглушка для файлов, загруженных пользователем через Telegram.

    Не скачивает файл — просто возвращает переданный путь.
    """

    def __init__(self, file_path: Path):
        self._file_path = file_path

    def download(self, vendor: str) -> Path:
        logger.info("[FIX] UploadDownloader: использую уже загруженный файл vendor=%s path=%s", vendor, self._file_path)
        return self._file_path
