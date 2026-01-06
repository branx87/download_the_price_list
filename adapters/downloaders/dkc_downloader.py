import requests
import logging
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
import time
import io
import zipfile
from adapters.downloaders.base_downloader import BaseDownloader
from config.settings import settings


logger = logging.getLogger(__name__)


class DkcDownloader(BaseDownloader):
    """Загрузчик для DKC с авторизацией"""
    
    def _get_download_url(self) -> str:
        """Возвращает базовый URL (требуется для абстрактного класса)"""
        return "https://www.dkc.ru/ru/personal/price/"
    
    def _login_to_dkc(self, session: requests.Session) -> requests.Session:
        """Авторизация на сайте DKC"""
        login_url = "https://www.dkc.ru/auth/"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.dkc.ru/ru/personal/price/',
        }
        
        try:
            logger.info("DKC: Получение страницы авторизации...")
            time.sleep(2)
            response = session.get(login_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем CSRF-токен
            csrf_token = None
            for name in ['sessid', 'csrf_token', 'bitrix_ajax']:
                input_tag = soup.find('input', {'name': name})
                if input_tag and input_tag.get('value'):
                    csrf_token = input_tag.get('value')
                    break
            
            # Данные для авторизации
            auth_data = {
                'AUTH_FORM': 'Y',
                'TYPE': 'AUTH',
                'USER_LOGIN': settings.DKC_LOGIN,
                'USER_PASSWORD': settings.DKC_PASSWORD,
                'Login': 'Войти',
                'backurl': '/ru/personal/price/'
            }
            
            if csrf_token:
                auth_data['sessid'] = csrf_token
            
            logger.info("DKC: Отправка данных авторизации...")
            time.sleep(2)
            response = session.post(login_url, data=auth_data, headers=headers, timeout=30)
            response.raise_for_status()
            
            if "Неверный логин или пароль" in response.text:
                raise Exception("Неверный логин или пароль")
            
            logger.info("DKC: Авторизация успешна")
            return session
            
        except Exception as e:
            logger.error(f"DKC: Ошибка авторизации: {e}")
            raise
    
    def _find_price_link(self, soup: BeautifulSoup) -> str:
        """Поиск ссылки на прайс-лист"""
        price_link = None
        
        # 1. Ищем по тексту ссылки
        for text in ['Прайс-лист', 'Скачать прайс', 'Price list']:
            a_tag = soup.find('a', string=lambda t: t and text.lower() in t.lower())
            if a_tag and a_tag.get('href'):
                price_link = a_tag['href']
                break
        
        # 2. Ищем в разделе загрузок
        if not price_link:
            downloads_div = soup.find('div', class_=lambda x: x and 'download' in x.lower())
            if downloads_div:
                for a in downloads_div.find_all('a', href=True):
                    if 'price' in a['href'].lower() or 'прайс' in a.get_text().lower():
                        price_link = a['href']
                        break
        
        # 3. Ищем по расширению файла
        if not price_link:
            for a in soup.find_all('a', href=lambda x: x and x.lower().endswith(('.xls', '.xlsx', '.zip'))):
                if 'price' in a.get_text().lower() or 'прайс' in a.get_text().lower():
                    price_link = a['href']
                    break
        
        if not price_link:
            raise Exception("Не удалось найти ссылку на прайс-лист")
        
        # Формируем полный URL
        if not price_link.startswith('http'):
            price_link = f"https://www.dkc.ru{price_link if price_link.startswith('/') else '/' + price_link}"
        
        return price_link
    
    def download(self, vendor: str) -> Path:
        """Загружает прайс-лист DKC с авторизацией"""
        logger.info(f"DKC: Начало загрузки для {vendor}")
        
        session = requests.Session()
        session.max_redirects = 5
        
        try:
            # Авторизация с повторными попытками
            for attempt in range(3):
                try:
                    session = self._login_to_dkc(session)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    logger.warning(f"DKC: Попытка {attempt + 1} не удалась, повтор...")
                    time.sleep(5)
            
            # Получаем страницу с прайсами
            price_page_url = self._get_download_url()
            logger.info(f"DKC: Получение страницы {price_page_url}")
            response = session.get(price_page_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем прямую ссылку на Excel
            excel_link = None
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                if href.endswith(('.xls', '.xlsx')) and 'price' in href:
                    excel_link = a['href']
                    if not excel_link.startswith('http'):
                        excel_link = f"https://www.dkc.ru{excel_link if excel_link.startswith('/') else '/' + excel_link}"
                    break
            
            if not excel_link:
                # Если нет прямой ссылки, ищем ZIP
                zip_link = self._find_price_link(soup)
                logger.info(f"DKC: Найдена ссылка на архив: {zip_link}")
                
                if zip_link.endswith('.zip'):
                    # Скачиваем и распаковываем ZIP
                    logger.info("DKC: Загрузка ZIP архива...")
                    zip_response = session.get(zip_link, stream=True, timeout=120)
                    zip_response.raise_for_status()
                    
                    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zip_ref:
                        excel_files = [f for f in zip_ref.namelist() 
                                     if f.lower().endswith(('.xls', '.xlsx'))]
                        
                        if not excel_files:
                            raise Exception("Excel файл не найден в архиве")
                        
                        excel_data = zip_ref.read(excel_files[0])
                        
                        # Сохраняем Excel
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                        ext = Path(excel_files[0]).suffix
                        filename = f"DKC_price_{timestamp}{ext}"
                        file_path = self.download_dir / filename
                        
                        file_path.write_bytes(excel_data)
                        file_size = len(excel_data)
                        
                        logger.info(f"DKC: Файл загружен из архива: {filename} ({file_size//1024} KB)")
                        return file_path
            else:
                # Прямая загрузка Excel
                logger.info(f"DKC: Прямая загрузка Excel: {excel_link}")
                response = session.get(excel_link, stream=True, timeout=120)
                response.raise_for_status()
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                filename = f"DKC_price_{timestamp}.xlsx"
                file_path = self.download_dir / filename
                
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                
                file_size = file_path.stat().st_size
                logger.info(f"DKC: Файл загружен: {filename} ({file_size//1024} KB)")
                return file_path
                
        except Exception as e:
            logger.error(f"DKC: Ошибка загрузки: {type(e).__name__}: {e}")
            raise