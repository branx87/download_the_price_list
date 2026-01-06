import logging
import zipfile
import io
from pathlib import Path
from bs4 import BeautifulSoup

from adapters.downloaders.base_downloader import BaseDownloader


logger = logging.getLogger(__name__)


class AuthHttpDownloader(BaseDownloader):
    """Загрузчик с авторизацией для DKC"""

    def __init__(self, download_dir: Path, login_url: str, 
                 username: str, password: str):
        super().__init__(download_dir)
        self.login_url = login_url
        self.username = username
        self.password = password
        self._is_authenticated = False

    def _get_download_url(self, vendor: str) -> str:
        """Получает URL после авторизации"""
        if not self._is_authenticated:
            self._authenticate()

        # Получаем страницу с прайсами
        response = self.session.get(
            "https://www.dkc.ru/ru/personal/price/",
            timeout=30
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Ищем ZIP ссылку
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '.zip' in href.lower() and 'price' in href.lower():
                if href.startswith('http'):
                    return href
                else:
                    return f"https://www.dkc.ru{href}"

        raise ValueError("Не найдена ссылка на прайс-лист DKC")

    def _authenticate(self):
        """Выполняет авторизацию"""
        logger.info("🔐 Авторизация на DKC")

        auth_data = {
            'AUTH_FORM': 'Y',
            'TYPE': 'AUTH',
            'USER_LOGIN': self.username,
            'USER_PASSWORD': self.password,
            'Login': 'Войти',
            'backurl': '/ru/personal/price/'
        }

        response = self.session.post(
            self.login_url,
            data=auth_data,
            timeout=30
        )
        response.raise_for_status()

        if "Неверный логин или пароль" in response.text:
            raise ValueError("Неверные учетные данные DKC")

        self._is_authenticated = True
        logger.info("✅ Авторизация успешна")

    def _download_file(self, url: str, vendor: str) -> Path:
        """Загружает и распаковывает ZIP"""
        # Загружаем ZIP
        response = self.session.get(url, stream=True, timeout=120)
        response.raise_for_status()

        # Распаковываем
        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
            excel_files = [
                f for f in zip_ref.namelist() 
                if f.lower().endswith(('.xlsx', '.xls'))
            ]

            if not excel_files:
                raise ValueError("В архиве нет Excel файлов")

            # Сохраняем первый Excel файл
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')
            filename = f"{vendor}_price_{timestamp}.xlsx"
            file_path = self.download_dir / filename

            excel_data = zip_ref.read(excel_files[0])
            with open(file_path, 'wb') as f:
                f.write(excel_data)

        return file_path
