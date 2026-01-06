from adapters.downloaders.base_downloader import BaseDownloader


class SimpleHttpDownloader(BaseDownloader):
    """Загрузчик для прямых HTTP ссылок"""

    def __init__(self, download_dir, url: str):
        super().__init__(download_dir)
        self.url = url

    def _get_download_url(self, vendor: str) -> str:
        """Возвращает настроенный URL"""
        return self.url
