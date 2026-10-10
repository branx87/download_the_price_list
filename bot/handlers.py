import asyncio
import functools
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
from decimal import Decimal
import subprocess
from pathlib import Path
import re
import httpx
from config.settings import settings
from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository
from domain.services.sync_service import SyncService
from domain.services.report_service import ReportService
from adapters.erp.erp_client import ErpClient
from domain.services.erp_sync_service import ErpSyncService
from adapters.downloaders.upload_downloader import UploadDownloader
from domain.entities.price_item import PriceItem


logger = logging.getLogger(__name__)


def admin_only(func):
    """Декоратор: допускает только пользователей из ADMIN_IDS."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        admin_ids = settings.ADMIN_IDS
        if admin_ids and update.effective_user.id not in admin_ids:
            logger.warning(
                "Отказ в доступе: user_id=%s username=%s",
                update.effective_user.id,
                update.effective_user.username,
            )
            await update.message.reply_text("⛔ Доступ запрещён.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# Хранилище последнего ErpSyncResult для генерации отчёта по кнопке
_last_erp_result = {
    'result': None,
}

# Кэш записей Total_Labor для пагинации и удаления по индексу
_labor_cache: list = []
_LABOR_PAGE_SIZE = 10

PROJECT_ROOT = settings.PROJECT_ROOT

sync_status = {
    'is_running': False,
    'current_vendor': None,
    'last_results': {}
}


def create_sync_service(vendor: str) -> SyncService:
    """Создает сервис синхронизации для проверки изменений"""
    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    repository = SqlRepository(settings.DATABASE_URL)
    report_service = ReportService(settings.PROJECT_ROOT / "reports")

    downloader = registry.create_downloader(vendor)
    parser = registry.create_parser(vendor)

    service = SyncService(
        downloader=downloader,
        parser=parser,
        repository=repository,
        price_change_threshold=settings.PRICE_CHANGE_THRESHOLD,
        report_service=report_service
    )

    return service


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"[START] user={user.first_name} chat_id={chat_id}")

    text = f"""🤖 Привет, {user.first_name}!
📌 Chat ID: {chat_id}

Команды:

/sync - Синхронизировать вендора
/sync_all - Синхронизировать всех
/check - Проверить актуальность прайса
/check_all - Проверить все прайсы
/erp - Обновить номенклатуру из 1C-ERP
/synonyms - Синонимы вендоров
/add_synonym - Добавить синоним
/del_synonym - Удалить синоним
/backfill_vff - Заполнить VendorForFilter
/duplicates - Найти и удалить дубли
/labor - Управление трудозатратами
/labor_edit - Изменить значение трудозатрат
/shina - Управление ценами шин (ШИНА)
/discontinued - Снятые с производства
/nsi <запрос> - Поиск номенклатуры (SQL → fallback на NSI/векторку)
/db_copy - Скопировать БД из MSSQL
/status - Статус
/debug - Показать ошибки
/help - Справка

📎 Файлы:
- Маппинг кодов 1С (.xls/.xlsx, имя любое — определяется по колонкам)"""

    await update.message.reply_text(text)


@admin_only
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /sync"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']

    keyboard = [[InlineKeyboardButton(f"🔄 {v}", callback_data=f"sync_{v}")] for v in vendors]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("📋 Выберите вендора для синхронизации:", reply_markup=reply_markup)


@admin_only
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /check - проверка актуальности прайсов"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']

    keyboard = [[InlineKeyboardButton(f"🔍 {v}", callback_data=f"check_{v}")] for v in vendors]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("📋 Выберите вендора для проверки:", reply_markup=reply_markup)


@admin_only
async def check_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /check_all - проверка актуальности всех прайсов"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']

    msg = await update.message.reply_text("🔍 Проверяю все прайсы...")

    summary_lines = ["📊 Проверка всех прайсов:\n"]

    for i, vendor in enumerate(vendors, 1):
        try:
            await msg.edit_text(f"🔍 {i}/{len(vendors)}: Проверяю {vendor}...")

            service = create_sync_service(vendor)
            result = service.check_price_changes(vendor)

            # Формируем краткую сводку
            if not result.has_changes:
                summary_lines.append(f"✅ {vendor}: актуален")
            else:
                changes = []
                if result.new_items_count > 0:
                    changes.append(f"➕{result.new_items_count}")
                if result.updated_items_count > 0:
                    changes.append(f"🔄{result.updated_items_count}")
                if result.disappeared_items_count > 0:
                    changes.append(f"👻{result.disappeared_items_count}")

                summary_lines.append(f"⚠️ {vendor}: {', '.join(changes)}")

        except Exception as e:
            logger.error(f"Ошибка проверки {vendor}: {e}", exc_info=True)
            summary_lines.append(f"❌ {vendor}: ошибка")

    summary = "\n".join(summary_lines)
    await msg.edit_text(summary)


@admin_only
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать последние ошибки"""
    debug_info = []

    # Проверяем last_error
    if sync_status.get('last_error'):
        debug_info.append(f"🔍 Last Error:\n{sync_status['last_error'][:800]}")

    # Показываем stdout и stderr последнего запуска
    if sync_status.get('last_stdout'):
        stdout = sync_status['last_stdout']
        # Ищем ошибки в stdout
        if 'ERROR' in stdout or 'Traceback' in stdout or '❌' in stdout:
            debug_info.append(f"\n📤 STDOUT (последние 1500 символов):\n{stdout[-1500:]}")
        else:
            debug_info.append(f"\n📤 STDOUT: OK (без ошибок)")

    if sync_status.get('last_stderr'):
        stderr = sync_status['last_stderr']
        if stderr.strip():  # Показываем только если есть содержимое
            debug_info.append(f"\n📥 STDERR:\n{stderr[-1000:]}")

    # Показываем return code
    if sync_status.get('last_returncode') is not None:
        code = sync_status['last_returncode']
        code_emoji = "✅" if code == 0 else "❌"
        debug_info.append(f"\n🔢 Return Code: {code} {code_emoji}")

    if not debug_info:
        await update.message.reply_text("ℹ️ Нет данных для отладки")
        return

    text = "\n".join(debug_info)

    # Telegram ограничивает длину сообщения до 4096 символов
    if len(text) > 4000:
        # Разбиваем на части
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i+4000])
    else:
        await update.message.reply_text(text)


def parse_sync_output(output: str, vendor: str):
    """Парсит вывод main.py и извлекает статистику.

    Returns: (total, new, updated, price_changes, disappeared)
    """
    total = new = updated = price_changes = disappeared = 0

    if not output:
        logger.warning(f"{vendor}: output is empty")
        return 0, 0, 0, 0, 0

    # Ищем строку: "KEAZ: total=31986, new=0, updated=5, price_changes=3, disappeared=0, time=5.0s"
    pattern = (
        rf"{vendor}:\s*total=(\d+),\s*new=(\d+),\s*updated=(\d+),\s*"
        rf"price_changes=(\d+),\s*disappeared=(\d+)"
    )

    for line in output.split('\n'):
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            total = int(match.group(1))
            new = int(match.group(2))
            updated = int(match.group(3))
            price_changes = int(match.group(4))
            disappeared = int(match.group(5))
            break

    return total, new, updated, price_changes, disappeared


@admin_only
async def sync_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /sync_all"""
    if sync_status['is_running']:
        await update.message.reply_text(f"⚠️ Уже идет: {sync_status['current_vendor']}")
        return

    sync_status['is_running'] = True

    msg = await update.message.reply_text("🚀 Запускаю синхронизацию...")

    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']

    for i, vendor in enumerate(vendors, 1):
        sync_status['current_vendor'] = vendor

        await msg.edit_text(f"🔄 {i}/{len(vendors)}: {vendor}")

        try:
            # Увеличенный таймаут для медленных вендоров
            timeout = 600  # Все вендоры - 10 мин (БД большая)

            result = subprocess.run(
                [settings.PYTHON_PATH, 'main.py', vendor],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(settings.PROJECT_ROOT),
                encoding='utf-8',
                errors='replace'
            )

            # Проверяем, что stdout/stderr не None
            stdout = result.stdout or ""
            stderr = result.stderr or ""

            sync_status['last_stdout'] = stdout
            sync_status['last_stderr'] = stderr
            sync_status['last_returncode'] = result.returncode

            if result.returncode == 0:
                output = stdout + stderr

                total, new, updated, price_changes, disappeared = parse_sync_output(output, vendor)
                logger.info(
                    f"[SYNC] {vendor}: total={total}, new={new}, updated={updated}, "
                    f"price_changes={price_changes}, disappeared={disappeared}"
                )

                sync_status['last_results'][vendor] = {
                    'success': True,
                    'total': total,
                    'new': new,
                    'updated': updated,
                    'price_changes': price_changes,
                    'disappeared': disappeared
                }

                # Проверяем успешность по наличию галочки в выводе
                success = '✅' in output or f'{vendor}:' in output

                if success and total > 0:
                    report_parts = [
                        f"✅ {vendor}",
                        f"📦 Всего: {total}",
                        f"➕ Новых: {new}",
                    ]
                    if price_changes > 0:
                        report_parts.append(f"🔄 Изменений цен: {price_changes}")
                    report_parts.append(f"👻 Исчезло: {disappeared}")
                    report = "\n".join(report_parts)
                else:
                    report = f"⚠️ {vendor}: Синхронизация выполнена, но данные не распознаны\nПроверь /debug"

            else:
                error_detail = f"Код: {result.returncode}\nStderr: {result.stderr[:300]}"
                sync_status['last_error'] = error_detail
                sync_status['last_results'][vendor] = {'success': False}
                report = f"❌ {vendor}: Ошибка\nИспользуй /debug"

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=report
            )

        except subprocess.TimeoutExpired:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⏱ {vendor}: Превышено время ожидания (5 мин)"
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            sync_status['last_error'] = error_msg
            logger.error(f"Ошибка {vendor}: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ {vendor}: {str(e)[:100]}"
            )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📊 Все вендоры обработаны!"
    )

    sync_status['is_running'] = False
    sync_status['current_vendor'] = None


@admin_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status"""
    if not sync_status['last_results']:
        await update.message.reply_text("ℹ️ Синхронизации еще не выполнялись")
        return

    text = "📊 Статус:\n\n"

    for vendor, result in sync_status['last_results'].items():
        emoji = "✅" if result.get('success') else "❌"
        text += f"{emoji} {vendor}\n"
        if result.get('success'):
            text += f"  Всего: {result.get('total', 0)}\n"
            text += f"  Новых: {result.get('new', 0)}\n"
            if result.get('price_changes', 0) > 0:
                text += f"  Изменений цен: {result.get('price_changes', 0)}\n"
            text += f"  Исчезло: {result.get('disappeared', 0)}\n"
        text += "\n"

    await update.message.reply_text(text)


@admin_only
async def vendors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /vendors"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']
    text = "📋 Вендоры:\n\n" + "\n".join(f"{i}. {v}" for i, v in enumerate(vendors, 1))
    await update.message.reply_text(text)


@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    text = """📚 Команды:

/sync - Синхронизировать вендора
/sync_all - Синхронизировать всех
/check - Проверить актуальность прайса
/check_all - Проверить все прайсы
/erp - Обновить номенклатуру из 1C-ERP
/synonyms - Показать синонимы вендоров
/add_synonym <Vendor> <VendorForFilter> - Добавить синоним
/del_synonym - Удалить синоним
/backfill_vff - Заполнить VendorForFilter
/duplicates - Найти и удалить дубли в Total_Price
/labor - Управление таблицей трудозатрат
/labor_edit <Category> <Value> - Изменить трудозатраты
/shina - Цены шин: /shina медь 1400  или  /shina алюм 500
/discontinued - Снятые с производства
/nsi <запрос> - Поиск номенклатуры (SQL → fallback на NSI/векторку)
  Примеры: /nsi TJ-15  ·  /nsi автомат 16А  ·  /nsi ABB S203-C16
/db_copy - Скопировать БД из MSSQL
/status - Статус синхронизаций
/debug - Показать ошибки
/help - Справка

📎 Загрузка файлов (отправь файл боту):
- Маппинг кодов 1С: .xls/.xlsx с колонками Артикул, Производитель, Код
  (имя файла любое — тип определяется по заголовкам)
- ШИНА: файл .xls с конфигурацией шин
- Снятые с производства: файл .xlsx/.csv

💡 Используйте /check чтобы посмотреть какие изменения будут при синхронизации"""

    await update.message.reply_text(text)


@admin_only
async def db_copy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /db_copy - скопировать БД из MSSQL в SQLite"""
    if not settings.MSSQL_SERVER:
        await update.message.reply_text("❌ MSSQL_SERVER не настроен в .env")
        return

    if sync_status['is_running']:
        await update.message.reply_text(f"⚠️ Уже идет операция: {sync_status['current_vendor']}")
        return

    sync_status['is_running'] = True
    sync_status['current_vendor'] = 'MSSQL копирование'

    # Формируем строку подключения MSSQL
    driver = "ODBC+Driver+18+for+SQL+Server"
    trust = "yes" if settings.MSSQL_TRUST_CERT == "yes" else "no"
    mssql_url = (
        f"mssql+pyodbc://{settings.MSSQL_USERNAME}:{settings.MSSQL_PASSWORD}"
        f"@{settings.MSSQL_SERVER}/{settings.MSSQL_DATABASE}"
        f"?driver={driver}&TrustServerCertificate={trust}"
    )

    # Файл назначения — рабочая БД бота
    output_file = str(settings.PROJECT_ROOT / "prices.db")

    await update.message.reply_text(
        f"🔄 Копирую БД из MSSQL...\n"
        f"📡 Сервер: {settings.MSSQL_SERVER}\n"
        f"📦 База: {settings.MSSQL_DATABASE}\n"
        f"💾 Файл: prices.db\n\n"
        f"Это может занять некоторое время..."
    )

    try:
        result = subprocess.run(
            ["db-to-sqlite", mssql_url, output_file, "--all", "-p"],
            capture_output=True,
            text=True,
            timeout=1800,  # 30 минут — копирование большой БД
            cwd=str(settings.PROJECT_ROOT),
            encoding='utf-8',
            errors='replace'
        )

        sync_status['last_stdout'] = result.stdout or ""
        sync_status['last_stderr'] = result.stderr or ""
        sync_status['last_returncode'] = result.returncode

        if result.returncode == 0:
            logger.info(f"[FIX] MSSQL -> SQLite копирование завершено успешно")

            # Инициализируем NULL статусы после копирования
            try:
                repo = SqlRepository(settings.DATABASE_URL)
                fixed = repo.fix_null_statuses()
                logger.info(f"[FIX] После db_copy: проставлен Status для {fixed} записей")
            except Exception as e:
                logger.warning(f"[FIX] Не удалось проставить статусы: {e}")

            # Получаем размер файла
            db_path = Path(output_file)
            size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0

            await update.message.reply_text(
                f"✅ БД скопирована!\n\n"
                f"💾 Файл: prices.db\n"
                f"📊 Размер: {size_mb:.1f} МБ\n"
                f"🔧 Статусы инициализированы: {fixed} записей"
            )
        else:
            logger.error(f"[FIX] MSSQL -> SQLite ошибка: {result.stderr[:500]}")
            stderr_snippet = (result.stderr or "")[-500:]
            await update.message.reply_text(
                f"❌ Ошибка копирования (код {result.returncode})\n\n"
                f"{stderr_snippet}\n\n"
                f"Используй /debug для деталей"
            )

    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱ Превышено время ожидания (30 мин)")
    except FileNotFoundError:
        logger.error("[FIX] db-to-sqlite не найден")
        await update.message.reply_text(
            "❌ db-to-sqlite не установлен!\n\n"
            "Установи: pip install db-to-sqlite pyodbc"
        )
    except Exception as e:
        logger.error(f"[FIX] Ошибка копирования MSSQL: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")
    finally:
        sync_status['is_running'] = False
        sync_status['current_vendor'] = None


@admin_only
async def erp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /erp - обновить номенклатуру из 1C-ERP"""
    if not settings.ERP_BASE_URL:
        await update.message.reply_text("❌ ERP_BASE_URL не настроен в .env")
        return

    keyboard = [
        [InlineKeyboardButton("📦 Обновить из 1C-ERP", callback_data="erp_sync")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🏭 Загрузка номенклатуры из 1C-ERP\n\n"
        "Будут добавлены только НОВЫЕ позиции.\n"
        "Существующие позиции не изменяются.",
        reply_markup=reply_markup
    )


async def _run_erp_sync(chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет синхронизацию с 1C-ERP и отправляет результат"""
    try:
        erp_client = ErpClient(
            base_url=settings.ERP_BASE_URL,
            login=settings.ONE_C_LOGIN,
            password=settings.ONE_C_PASSWORD
        )
        repository = SqlRepository(settings.DATABASE_URL)
        service = ErpSyncService(erp_client, repository)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, service.sync_from_erp)

        logger.info(
            f"[ERP] Синхронизация завершена: received={result.total_received}, "
            f"added={result.added}, linked={result.updated}, skipped={result.skipped_existing}, "
            f"duplicates={result.skipped_duplicates}, errors={result.errors}"
        )

        # Формируем отчет
        report_lines = ["📊 Результат загрузки из 1C-ERP:"]
        report_lines.append(f"\n📥 Получено из 1C: {result.total_received}")
        report_lines.append(f"➕ Добавлено новых: {result.added}")
        report_lines.append(f"🔗 Привязано ArticlePC: {result.updated}")
        report_lines.append(f"⏭ Пропущено (уже есть): {result.skipped_existing}")

        if result.skipped_duplicates > 0:
            report_lines.append(f"\n⚠️ Дубликатов кодов: {result.skipped_duplicates}")
            if result.duplicate_codes:
                codes_preview = result.duplicate_codes[:5]
                report_lines.append("Коды: " + ", ".join(codes_preview))
                if len(result.duplicate_codes) > 5:
                    report_lines.append(f"  ... и ещё {len(result.duplicate_codes) - 5}")

        if result.has_errors:
            report_lines.append(f"\n❌ Ошибок: {result.errors}")
            for detail in result.error_details[:3]:
                report_lines.append(f"  - {detail[:100]}")

        report = "\n".join(report_lines)

        if len(report) > 4000:
            report = report[:3900] + "\n\n... (обрезано)"

        await context.bot.send_message(chat_id=chat_id, text=report)

        # Если есть детали — предлагаем отчёт вместо спама сообщениями
        if result.added_details or result.linked_details:
            _last_erp_result['result'] = result
            keyboard = [[InlineKeyboardButton("📊 Нужен отчёт?", callback_data="erp_report")]]
            await context.bot.send_message(
                chat_id=chat_id,
                text="Детальный отчёт доступен в формате Excel.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Ошибка синхронизации 1C-ERP: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка загрузки из 1C-ERP: {str(e)[:300]}"
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query

    # Проверка доступа для callback-кнопок
    admin_ids = settings.ADMIN_IDS
    if admin_ids and update.effective_user.id not in admin_ids:
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from telegram.error import BadRequest as TgBadRequest
    try:
        await query.answer()
    except TgBadRequest:
        # Колбэк устарел (бот был перезапущен) — просто игнорируем
        return

    if query.data == 'labor_noop':
        return

    if query.data.startswith('mapping_add_yes_') or query.data.startswith('mapping_add_no_'):
        decision = 'yes' if query.data.startswith('mapping_add_yes_') else 'no'
        try:
            await _handle_mapping_add_callback(query, context, decision)
        except Exception as e:
            logger.error("[MAPPING] add callback error: %s", e, exc_info=True)
            try:
                await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
            except Exception:
                pass
        return

    if query.data.startswith('nsi_add_'):
        try:
            idx = int(query.data.replace('nsi_add_', ''))
            await _nsi_add_callback(query, context, idx)
        except (ValueError, Exception) as e:
            logger.error("[NSI] add callback error: %s", e, exc_info=True)
            try:
                await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
            except Exception:
                pass
        return

    if query.data == 'nsi_close':
        try:
            # Убираем клавиатуру, текст не трогаем.
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.debug("[NSI] close: %s", e)
        return

    if query.data.startswith('labor_page_'):
        page = int(query.data.replace('labor_page_', ''))
        await _send_labor_page(query, page)
        return

    if query.data.startswith('labor_del_'):
        idx = int(query.data.replace('labor_del_', ''))
        if not _labor_cache or idx >= len(_labor_cache):
            await query.edit_message_text("⚠️ Список устарел. Выполните /labor заново.")
            return
        item = _labor_cache[idx]
        category = item['category']
        try:
            repository = SqlRepository(settings.DATABASE_URL)
            repository.delete_labor_item(category)
            _labor_cache.pop(idx)
            if _labor_cache:
                page = idx // _LABOR_PAGE_SIZE
                total_pages = (len(_labor_cache) + _LABOR_PAGE_SIZE - 1) // _LABOR_PAGE_SIZE
                page = min(page, total_pages - 1)
                await _send_labor_page(query, page)
            else:
                await query.edit_message_text("✅ Все записи удалены. Таблица пуста.")
        except Exception as e:
            logger.error(f"Ошибка удаления labor: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
        return

    if query.data == 'dup_preview':
        repository = SqlRepository(settings.DATABASE_URL)
        samples = repository.get_duplicates_sample()
        if not samples:
            await query.edit_message_text("✅ Дублей (по Вендор+Арт) не найдено!")
            return
        lines = ["🔍 Дубли по Производитель+Артикул (топ 10):\n"]
        for s in samples:
            lines.append(f"• {s['vendor']} | {s['part_num']}  — {s['count']} шт.")
        keyboard = [[InlineKeyboardButton("🗑 Удалить все дубли (Вендор+Арт)", callback_data="dup_delete")]]
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data == 'dup_apc_preview':
        repository = SqlRepository(settings.DATABASE_URL)
        samples = repository.get_duplicates_sample_by_article_pc()
        if not samples:
            await query.edit_message_text("✅ Дублей по ArticlePC не найдено!")
            return
        lines = ["🔍 Дубли по ArticlePC (топ 10):\n"]
        for s in samples:
            lines.append(f"• {s['article_pc']}  — {s['count']} записей")
        keyboard = [[InlineKeyboardButton("🗑 Удалить все дубли (ArticlePC)", callback_data="dup_apc_delete")]]
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data == 'dup_delete':
        await query.edit_message_text("⏳ Удаляю дубли по Производитель+Артикул...")
        try:
            repository = SqlRepository(settings.DATABASE_URL)
            deleted, deleted_items = repository.delete_duplicates()
            lines = [
                f"✅ Готово!\n",
                f"🗑 Удалено строк: {deleted}",
                f"💾 Сохранены строки с ArticlePC или самые свежие.\n",
            ]
            if deleted_items:
                lines.append(f"Удалённые записи (первые {len(deleted_items)}):")
                for item in deleted_items:
                    apc = f" [{item['article_pc']}]" if item['article_pc'] else ""
                    lines.append(f"• {item['vendor']} | {item['part_num']}{apc}")
                if deleted > len(deleted_items):
                    lines.append(f"... и ещё {deleted - len(deleted_items)}")
            report = "\n".join(lines)
            if len(report) > 4000:
                report = report[:3900] + "\n\n... (обрезано)"
            await query.edit_message_text(report)
        except Exception as e:
            logger.error(f"Ошибка удаления дублей: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
        return

    if query.data == 'dup_apc_delete':
        await query.edit_message_text("⏳ Удаляю дубли по ArticlePC...")
        try:
            repository = SqlRepository(settings.DATABASE_URL)
            deleted, deleted_items = repository.delete_duplicates_by_article_pc()
            lines = [
                f"✅ Готово!\n",
                f"🗑 Удалено строк: {deleted}",
                f"💾 Сохранены строки с Вендором или самые свежие.\n",
            ]
            if deleted_items:
                lines.append(f"Удалённые записи (первые {len(deleted_items)}):")
                for item in deleted_items:
                    lines.append(f"• {item['article_pc']} | {item['vendor']} | {item['part_num']}")
                if deleted > len(deleted_items):
                    lines.append(f"... и ещё {deleted - len(deleted_items)}")
            report = "\n".join(lines)
            if len(report) > 4000:
                report = report[:3900] + "\n\n... (обрезано)"
            await query.edit_message_text(report)
        except Exception as e:
            logger.error(f"Ошибка удаления дублей по ArticlePC: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
        return

    if query.data.startswith('del_syn_'):
        syn_id = int(query.data.replace('del_syn_', ''))
        repository = SqlRepository(settings.DATABASE_URL)
        deleted = repository.delete_synonym(syn_id)
        if deleted:
            await query.edit_message_text(f"Синоним ID={syn_id} удален.")
        else:
            await query.edit_message_text(f"Синоним ID={syn_id} не найден.")
        return

    if query.data == 'erp_sync':
        if sync_status['is_running']:
            await query.edit_message_text(f"⚠️ Уже идет операция: {sync_status['current_vendor']}")
            return

        sync_status['is_running'] = True
        sync_status['current_vendor'] = '1C-ERP'

        await query.edit_message_text("🔄 Загрузка номенклатуры из 1C-ERP...")

        try:
            await _run_erp_sync(query.message.chat_id, query.message.message_id, context)
        finally:
            sync_status['is_running'] = False
            sync_status['current_vendor'] = None

        return

    if query.data == 'erp_report':
        erp_result = _last_erp_result.get('result')
        if not erp_result:
            await query.edit_message_text("⚠️ Нет данных для отчёта. Сначала выполните /erp")
            return

        await query.edit_message_text("📊 Формирую Excel-отчёт...")

        try:
            report_service = ReportService(settings.PROJECT_ROOT / "reports")
            filepath = report_service.create_erp_report(erp_result)
            logger.info(f"[FIX] ERP-отчёт создан: {filepath}")

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=open(filepath, 'rb'),
                filename=filepath.name,
                caption="📊 Отчёт ERP-синхронизации"
            )
            _last_erp_result['result'] = None
        except Exception as e:
            logger.error(f"Ошибка генерации ERP-отчёта: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Ошибка создания отчёта: {str(e)[:200]}"
            )
        return

    if query.data == 'disc_list_menu':
        await query.edit_message_text(
            "📋 Выберите вендора:", reply_markup=_disc_vendor_keyboard("disc_list_")
        )
        return

    if query.data.startswith('disc_list_'):
        vendor_key = query.data.replace('disc_list_', '')
        vendor_filter = None if vendor_key == 'ALL' else vendor_key
        try:
            repository = SqlRepository(settings.DATABASE_URL)
            items = repository.get_discontinued_items(vendor_filter)
        except Exception as e:
            logger.error("[DISCONTINUED] disc_list: %s", e, exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
            return
        if not items:
            await query.edit_message_text(
                "ℹ️ Снятых с производства не найдено.",
                reply_markup=_disc_main_keyboard()
            )
            return
        label = vendor_key if vendor_key != 'ALL' else 'ВСЕ'
        lines = [f"📵 Снятые с производства [{label}]:\n"]
        for item in items[:_DISC_PAGE_SIZE]:
            replacement = f" (→ {item['storage']})" if item['storage'] else ""
            lines.append(f"• {item['vendor']} | {item['part_num']}{replacement}")
        if len(items) > _DISC_PAGE_SIZE:
            lines.append(f"\n... и ещё {len(items) - _DISC_PAGE_SIZE}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n..."
        await query.edit_message_text(text, reply_markup=_disc_main_keyboard())
        return

    if query.data == 'disc_mark_start':
        context.user_data['disc_state'] = 'awaiting_mark'
        await query.edit_message_text(
            "✅ Введите: <b>VENDOR ARTICLE [REPLACEMENT]</b>\n\n"
            "Пример: <code>KEAZ ABC123</code>\n"
            "С заменой: <code>KEAZ ABC123 XYZ456</code>",
            parse_mode='HTML'
        )
        return

    if query.data == 'disc_unmark_start':
        context.user_data['disc_state'] = 'awaiting_unmark'
        await query.edit_message_text(
            "↩️ Введите: <b>VENDOR ARTICLE</b>\n\n"
            "Пример: <code>KEAZ ABC123</code>",
            parse_mode='HTML'
        )
        return

    if query.data.startswith('check_'):
        vendor = query.data.replace('check_', '')

        await query.edit_message_text(f"🔍 Проверяю актуальность {vendor}...")

        try:
            service = create_sync_service(vendor)
            result = service.check_price_changes(vendor)

            # Формируем отчет
            report_lines = [f"📊 Проверка {vendor}:"]

            if result.last_db_update:
                days_ago = (datetime.now() - result.last_db_update).days
                report_lines.append(f"\n⏰ Последнее обновление: {result.last_db_update.strftime('%d.%m.%Y %H:%M')}")
                report_lines.append(f"   ({days_ago} дн. назад)")

            report_lines.append(f"\n📦 В БД: {result.total_in_db}")
            report_lines.append(f"📥 В файле: {result.total_in_file}")

            if not result.has_changes:
                report_lines.append("\n✅ Изменений нет, прайс актуален")
            else:
                report_lines.append(f"\n⚠️ Обнаружены изменения:")

                if result.new_items_count > 0:
                    report_lines.append(f"\n➕ Новых позиций: {result.new_items_count}")

                if result.updated_items_count > 0:
                    report_lines.append(f"\n🔄 Изменений цен: {result.updated_items_count}")

                    if result.avg_price_change_percent != 0:
                        report_lines.append(f"   Средн. изменение: {result.avg_price_change_percent:+.1f}%")

                    max_increase = result.max_price_increase
                    if max_increase:
                        report_lines.append(f"   Макс. рост: {max_increase.price_diff_percent:+.1f}%")
                        report_lines.append(f"   ({max_increase.article})")

                    max_decrease = result.max_price_decrease
                    if max_decrease:
                        report_lines.append(f"   Макс. снижение: {max_decrease.price_diff_percent:+.1f}%")
                        report_lines.append(f"   ({max_decrease.article})")

                if result.disappeared_items_count > 0:
                    report_lines.append(f"\n👻 Исчезло позиций: {result.disappeared_items_count}")

                report_lines.append(f"\n💡 Используйте /sync для обновления")

            report = "\n".join(report_lines)

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=report
            )

        except Exception as e:
            logger.error(f"Ошибка проверки {vendor}: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Ошибка при проверке {vendor}: {str(e)[:200]}"
            )

    elif query.data.startswith('sync_'):
        vendor = query.data.replace('sync_', '')

        if sync_status['is_running']:
            await query.edit_message_text(f"⚠️ Идет: {sync_status['current_vendor']}")
            return

        sync_status['is_running'] = True
        sync_status['current_vendor'] = vendor

        await query.edit_message_text(f"🚀 Синхронизация {vendor}...")

        try:
            # Увеличенный таймаут для медленных вендоров
            timeout = 600  # Все вендоры - 10 мин (БД большая)

            result = subprocess.run(
                [settings.PYTHON_PATH, 'main.py', vendor],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(PROJECT_ROOT),
                encoding='utf-8',
                errors='replace'
            )
            
            # Проверяем, что stdout/stderr не None
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            
            # Всегда сохраняем вывод
            sync_status['last_stdout'] = stdout
            sync_status['last_stderr'] = stderr
            sync_status['last_returncode'] = result.returncode
            
            if result.returncode == 0:
                output = stdout + stderr

                total, new, updated, price_changes, disappeared = parse_sync_output(output, vendor)
                logger.info(
                    f"[SYNC] {vendor}: total={total}, new={new}, updated={updated}, "
                    f"price_changes={price_changes}, disappeared={disappeared}"
                )

                sync_status['last_results'][vendor] = {
                    'success': True,
                    'total': total,
                    'new': new,
                    'updated': updated,
                    'price_changes': price_changes,
                    'disappeared': disappeared,
                }

                success = '✅' in output or f'{vendor}:' in output

                if success and total > 0:
                    report_parts = [
                        f"✅ {vendor} готово!",
                        f"",
                        f"📦 Всего: {total}",
                        f"➕ Новых: {new}",
                    ]
                    if price_changes > 0:
                        report_parts.append(f"🔄 Изменений цен: {price_changes}")
                    report_parts.append(f"👻 Исчезло: {disappeared}")
                    report = "\n".join(report_parts)
                else:
                    # ИСПРАВИЛ: ограничил длину
                    stderr_snippet = stderr[-300:] if stderr else "нет stderr"
                    report = f"⚠️ {vendor}: Выполнено, но данные не распознаны\n\nПоследние строки stderr:\n{stderr_snippet}"
            
            else:
                # ИСПРАВИЛ: ограничил длину для last_error
                error_detail = f"Return code: {result.returncode}\n\nSTDERR:\n{stderr[:2000]}\n\nSTDOUT:\n{stdout[:2000]}"
                sync_status['last_error'] = error_detail
                sync_status['last_results'][vendor] = {'success': False}
                
                # Показываем краткую версию в чате
                brief_error = stderr[-500:] if stderr else "Нет stderr"
                report = f"❌ {vendor}: Ошибка (код {result.returncode})\n\n{brief_error}\n\nПолный вывод: /debug"
            
            # ← ДОБАВИЛ: Проверка длины и отправка сообщения
            if len(report) > 4000:
                report = report[:3900] + "\n\n... (обрезано, используй /debug)"
            
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=report
            )

        except subprocess.TimeoutExpired:
            error_msg = f"{vendor}: Timeout после 5 минут"
            sync_status['last_error'] = error_msg
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"⏱ {error_msg}"
            )
            
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            sync_status['last_error'] = error_msg
            logger.error(f"Ошибка {vendor}: {e}", exc_info=True)
            
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ {vendor}: {error_msg}\n\nИспользуй /debug"
            )

        finally:
            sync_status['is_running'] = False
            sync_status['current_vendor'] = None


@admin_only
async def synonyms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /synonyms - показать все синонимы вендоров"""
    repository = SqlRepository(settings.DATABASE_URL)
    synonyms = repository.get_all_synonyms()

    if not synonyms:
        await update.message.reply_text("Синонимов пока нет.\n\nДобавить: /add_synonym <Vendor> <VendorForFilter>")
        return

    lines = ["Синонимы вендоров:\n"]
    current_vff = None
    for s in synonyms:
        if s['vendor_for_filter'] != current_vff:
            current_vff = s['vendor_for_filter']
            lines.append(f"\n[{current_vff}]")
        lines.append(f"  {s['vendor']} (ID: {s['id']})")

    await update.message.reply_text("\n".join(lines))


@admin_only
@admin_only
async def add_synonym_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /add_synonym <Vendor> <VendorForFilter>"""
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Использование: /add_synonym <Vendor> <VendorForFilter>\n\n"
            "Примеры:\n"
            "/add_synonym ABB-AZ ABB\n"
            "/add_synonym ABB-KZ ABB\n"
            "/add_synonym АББ ABB"
        )
        return

    vendor = args[0]
    vendor_for_filter = ' '.join(args[1:])

    try:
        repository = SqlRepository(settings.DATABASE_URL)
        repository.add_synonym(vendor, vendor_for_filter)
        await update.message.reply_text(f"Добавлен синоним: {vendor} -> {vendor_for_filter}")
    except Exception as e:
        logger.error(f"Ошибка добавления синонима: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка: {str(e)[:200]}")


@admin_only
@admin_only
async def del_synonym_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /del_synonym - удалить синоним через inline-кнопки"""
    repository = SqlRepository(settings.DATABASE_URL)
    synonyms = repository.get_all_synonyms()

    if not synonyms:
        await update.message.reply_text("Синонимов нет.")
        return

    keyboard = [
        [InlineKeyboardButton(
            f"X  {s['vendor']} -> {s['vendor_for_filter']}",
            callback_data=f"del_syn_{s['id']}"
        )]
        for s in synonyms
    ]
    await update.message.reply_text(
        "Выберите синоним для удаления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _send_labor_page(target, page: int):
    """Отправляет или обновляет страницу трудозатрат.
    target — Message (при первом вызове) или CallbackQuery (при навигации).
    """
    from telegram import Message as TgMessage
    items = _labor_cache
    if not items:
        text = "⚠️ Список устарел. Выполните /labor заново."
        if isinstance(target, TgMessage):
            await target.reply_text(text)
        else:
            await target.edit_message_text(text)
        return

    total_pages = (len(items) + _LABOR_PAGE_SIZE - 1) // _LABOR_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * _LABOR_PAGE_SIZE
    page_items = items[start:start + _LABOR_PAGE_SIZE]

    lines = [f"📋 Трудозатраты (стр. {page + 1}/{total_pages}, всего {len(items)}):\n"]
    for i, item in enumerate(page_items):
        cat = item['category'] or '(пусто)'
        val = item['labor']
        lines.append(f"{start + i + 1}. {cat} — {val}")

    keyboard = [
        [InlineKeyboardButton(
            f"🗑 {item['category'] or '(пусто)'}",
            callback_data=f"labor_del_{start + i}"
        )]
        for i, item in enumerate(page_items)
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Назад", callback_data=f"labor_page_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="labor_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Вперед ▶", callback_data=f"labor_page_{page + 1}"))
    keyboard.append(nav)

    text = "\n".join(lines)
    reply_markup = InlineKeyboardMarkup(keyboard)

    if isinstance(target, TgMessage):
        await target.reply_text(text, reply_markup=reply_markup)
    else:
        await target.edit_message_text(text, reply_markup=reply_markup)


@admin_only
async def labor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /labor - просмотр и удаление записей Total_Labor"""
    global _labor_cache
    repository = SqlRepository(settings.DATABASE_URL)
    _labor_cache = repository.get_labor_items()

    if not _labor_cache:
        await update.message.reply_text("📋 Таблица Total_Labor пуста.")
        return

    await _send_labor_page(update.message, 0)


@admin_only
async def labor_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /labor_edit <Category> <NewValue> - изменить значение трудозатрат.
    Последний аргумент — новое значение, всё перед ним — название категории.
    Пример: /labor_edit Корпуса сборка 6.588
    """
    global _labor_cache
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Использование: /labor_edit <Category> <Value>\n\n"
            "Примеры:\n"
            "/labor_edit АВ-М-1-1П 0.324\n"
            "/labor_edit Корпуса сборка 6.588\n\n"
            "Последнее слово — новое значение.\n"
            "Всё перед ним — название категории."
        )
        return

    try:
        new_value = float(args[-1].replace(',', '.'))
        category = ' '.join(args[:-1])
    except ValueError:
        await update.message.reply_text(f"❌ Некорректное значение: {args[-1]!r}")
        return

    try:
        repository = SqlRepository(settings.DATABASE_URL)
        updated = repository.update_labor_item(category, new_value)
        if updated:
            _labor_cache = repository.get_labor_items()
            await update.message.reply_text(f"✅ Обновлено: {category} → {new_value}")
        else:
            await update.message.reply_text(
                f"⚠️ Категория не найдена: {category!r}\n\n"
                f"Проверьте название через /labor"
            )
    except Exception as e:
        logger.error(f"Ошибка labor_edit: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")


@admin_only
async def duplicates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /duplicates - найти и удалить дубли в Total_Price"""
    repository = SqlRepository(settings.DATABASE_URL)
    info_vp = repository.get_duplicates_info()
    info_apc = repository.get_duplicates_info_by_article_pc()

    if info_vp['groups'] == 0 and info_apc['groups'] == 0:
        await update.message.reply_text("✅ Дублей в Total_Price не найдено.")
        return

    lines = ["⚠️ Найдены дубли в Total_Price:\n"]

    keyboard = []

    if info_vp['groups'] > 0:
        lines.append(
            f"📦 По Производитель+Артикул:\n"
            f"  Групп: {info_vp['groups']}, лишних строк: {info_vp['excess_rows']}\n"
            f"  (сохраняется строка с ArticlePC или самая свежая)"
        )
        keyboard.append([
            InlineKeyboardButton("🔍 Примеры (Вендор+Арт)", callback_data="dup_preview"),
            InlineKeyboardButton("🗑 Удалить (Вендор+Арт)", callback_data="dup_delete"),
        ])

    if info_apc['groups'] > 0:
        lines.append(
            f"\n🔑 По ArticlePC (код 1С):\n"
            f"  Групп: {info_apc['groups']}, лишних строк: {info_apc['excess_rows']}\n"
            f"  (сохраняется строка с Вендором или самая свежая)"
        )
        keyboard.append([
            InlineKeyboardButton("🔍 Примеры (ArticlePC)", callback_data="dup_apc_preview"),
            InlineKeyboardButton("🗑 Удалить (ArticlePC)", callback_data="dup_apc_delete"),
        ])

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_only
async def backfill_vff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /backfill_vff - заполнить VendorForFilter для всех записей"""
    await update.message.reply_text("Заполняю VendorForFilter...")

    try:
        repository = SqlRepository(settings.DATABASE_URL)
        synonyms_map = repository.get_synonyms_map()
        count = repository.backfill_vendor_for_filter(synonyms_map)
        await update.message.reply_text(f"Заполнено VendorForFilter для {count} записей.")
    except Exception as e:
        logger.error(f"Ошибка backfill: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка: {str(e)[:300]}")


# ─── ШИНА ─────────────────────────────────────────────────────────────────────

@admin_only
async def shina_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /shina [медь|алюм <цена>]

    /shina               — показать текущие цены
    /shina медь 1400     — обновить цену меди и пересчитать прайс
    /shina алюм 500      — обновить цену алюминия и пересчитать прайс
    """
    from domain.services.shina_service import ShinaService

    repository = SqlRepository(settings.DATABASE_URL)
    service = ShinaService(repository, None)

    args = context.args

    if not args:
        try:
            prices = service.get_current_prices()
            count  = service.get_config_count()
        except Exception as e:
            logger.error("[SHINA] shina_command get_prices: %s", e, exc_info=True)
            await update.message.reply_text(f"❌ Ошибка чтения shina_config: {str(e)[:200]}")
            return

        if not prices:
            await update.message.reply_text(
                "⚠️ shina_config пуст.\n\n"
                "Отправьте Excel-файл шин боту (имя файла должно содержать «shina»)."
            )
            return

        lines = ["📊 Текущие цены шин:\n"]
        if 'медь' in prices:
            lines.append(f"🔴 Медь: {prices['медь']} руб/кг")
        if 'алюм' in prices:
            lines.append(f"⚪ Алюминий: {prices['алюм']} руб/кг")
        lines.append(f"\n📦 Позиций в конфиге: {count}")
        lines.append("\nОбновить: /shina медь 1400  или  /shina алюм 500")
        await update.message.reply_text("\n".join(lines))
        return

    if len(args) != 2:
        await update.message.reply_text(
            "Использование:\n"
            "/shina — текущие цены\n"
            "/shina медь 1400 — обновить цену меди\n"
            "/shina алюм 500 — обновить цену алюминия"
        )
        return

    material_input = args[0]
    try:
        price_per_kg = float(args[1].replace(',', '.'))
    except ValueError:
        await update.message.reply_text(f"❌ Некорректная цена: {args[1]!r}")
        return

    if price_per_kg <= 0:
        await update.message.reply_text("❌ Цена должна быть больше 0")
        return

    msg = await update.message.reply_text(f"🔄 Обновляю цену {material_input}...")
    try:
        loop = asyncio.get_event_loop()
        updated = await loop.run_in_executor(None, service.update_price, material_input, price_per_kg)

        if updated == 0:
            await msg.edit_text(
                f"⚠️ Материал «{material_input}» не найден в shina_config.\n\n"
                "Сначала загрузите Excel-файл шин."
            )
        else:
            await msg.edit_text(
                f"✅ Цена обновлена!\n\n"
                f"Материал: {material_input}\n"
                f"Цена за кг: {price_per_kg} руб\n"
                f"Обновлено позиций в Total_Price: {updated}"
            )
    except ValueError as e:
        await msg.edit_text(f"❌ {e}")
    except Exception as e:
        logger.error("[SHINA] shina_command update: %s", e, exc_info=True)
        await msg.edit_text(f"❌ Ошибка: {str(e)[:300]}")


# ─── Маппинг артикул+производитель → код 1С ────────────────────────────────────

# Префикс ключа в context.user_data для кэша ненайденных позиций.
# Полный ключ: f'{_MAPPING_PENDING_KEY_PREFIX}{message_id}'
_MAPPING_PENDING_KEY_PREFIX = 'mapping_pending_'


def _looks_like_mapping_file(file_path: Path) -> bool:
    """True, если файл является маппингом 1С по ЗАГОЛОВКАМ колонок.

    Используется для определения типа файла без зависимости от имени.
    Маппинг = есть Артикул + Код/Артикул РС + Производитель.
    """
    try:
        from adapters.parsers.mapping_parser import MappingParser
        return MappingParser().detect(file_path)
    except Exception as e:
        logger.warning("[MAPPING] _looks_like_mapping_file: %s", e)
        return False


def _build_mapping_pending_key(message_id: int) -> str:
    return f'{_MAPPING_PENDING_KEY_PREFIX}{message_id}'


async def _handle_mapping_upload(update: Update, file_path: Path, context: ContextTypes.DEFAULT_TYPE):
    """Загружает маппинг артикул+производитель -> код 1С из Excel (.xls/.xlsx).

    Логика:
      1. Парсим файл (по заголовкам, без привязки к имени).
      2. Делаем bulk UPDATE ArticlePC для найденных пар (manufacturer, article).
      3. Если есть ненайденные — кладём их в context.user_data и
         показываем инлайн-кнопки «Да, добавить N шт.» / «Нет».
    """
    from adapters.parsers.mapping_parser import MappingParser

    msg = await update.message.reply_text("🔄 Загружаю маппинг артикулов...")
    try:
        parser = MappingParser()
        items = parser.parse(file_path)

        if not items:
            await msg.edit_text("⚠️ Файл не содержит валидных записей.")
            return

        repository = SqlRepository(settings.DATABASE_URL)
        updated, not_found = await asyncio.get_event_loop().run_in_executor(
            None, repository.bulk_update_article_pc_from_mapping, items
        )

        lines = [
            f"✅ <b>Маппинг загружен!</b>\n",
            f"📥 Записей в файле: {len(items)}",
            f"🔗 Обновлено ArticlePC: {updated}",
        ]
        reply_markup = None
        if not_found:
            lines.append(f"⚠️ Не найдено в БД: {len(not_found)}")
            for nf in not_found[:10]:
                lines.append(f"  • {nf['manufacturer']} | {nf['article']}")
            if len(not_found) > 10:
                lines.append(f"  ... и ещё {len(not_found) - 10}")

            # Кладём ненайденные в user_data, чтобы кнопки могли их достать.
            token = msg.message_id
            context.user_data[_build_mapping_pending_key(token)] = not_found

            lines.append("\n❓ <b>Добавить эти позиции в БД?</b>")
            lines.append("Цена будет выставлена «Цена по запросу» (Price=0).")
            reply_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        f"✅ Да, добавить {len(not_found)} шт.",
                        callback_data=f"mapping_add_yes_{token}",
                    ),
                    InlineKeyboardButton(
                        "❌ Нет",
                        callback_data=f"mapping_add_no_{token}",
                    ),
                ],
            ])

        await msg.edit_text("\n".join(lines), parse_mode='HTML', reply_markup=reply_markup)
        logger.info("[MAPPING] upload: total=%d updated=%d not_found=%d",
                    len(items), updated, len(not_found))
    except Exception as e:
        logger.error("[MAPPING] _handle_mapping_upload: %s", e, exc_info=True)
        await msg.edit_text(f"❌ Ошибка загрузки маппинга:\n{str(e)[:300]}")


async def _handle_mapping_add_callback(query, context: ContextTypes.DEFAULT_TYPE, decision: str):
    """Обрабатывает нажатие «Да/Нет» на запрос добавления ненайденных позиций.

    decision: 'yes' | 'no'
    """
    callback_data = query.data or ''
    parts = callback_data.rsplit('_', 1)
    if len(parts) != 2:
        await query.edit_message_text("⚠️ Устаревшая кнопка. Загрузите файл заново.")
        return
    try:
        token = int(parts[1])
    except ValueError:
        await query.edit_message_text("⚠️ Устаревшая кнопка. Загрузите файл заново.")
        return

    key = _build_mapping_pending_key(token)
    not_found = context.user_data.pop(key, None)

    if not not_found:
        await query.edit_message_text(
            "⚠️ Список ненайденных позиций устарел или уже обработан.\n"
            "Загрузите файл маппинга заново."
        )
        return

    if decision == 'no':
        await query.edit_message_text(
            f"🚫 Ок, не добавляю. Пропущено позиций: <b>{len(not_found)}</b>.",
            parse_mode='HTML',
        )
        logger.info("[MAPPING] user declined add: skipped=%d", len(not_found))
        return

    # decision == 'yes': добавляем все позиции одним батчем.
    def _do_add():
        repository = SqlRepository(settings.DATABASE_URL)
        price_items = [
            PriceItem(
                vendor=item['manufacturer'],
                article=item['article'],
                description=item.get('description', ''),
                price=Decimal(0),  # → PriceText='Цена по запросу' в add_items
                units=item.get('units') or 'шт',
                storage='',
                code_1c=item['code'],
            )
            for item in not_found
        ]
        added = repository.add_items(price_items)
        return added, len(price_items)

    added, total = await asyncio.get_event_loop().run_in_executor(None, _do_add)

    lines = [
        "✅ <b>Добавление завершено!</b>\n",
        f"➕ Добавлено в БД: <b>{added}</b> из {total}",
        f"💬 Цена: <i>«Цена по запросу»</i> (Price=0, PriceText заполнен автоматически)",
        "",
        "Первые позиции:",
    ]
    for nf in not_found[:5]:
        lines.append(f"  • {nf['manufacturer']} | {nf['article']} → {nf['code']}")
    if total > 5:
        lines.append(f"  ... и ещё {total - 5}")

    await query.edit_message_text("\n".join(lines), parse_mode='HTML')
    logger.info("[MAPPING] user accepted add: total=%d added=%d", total, added)


# ─── NSI: поиск по векторной БД номенклатуры 1С (engineering-assistant) ───────

# Префикс ключа в context.user_data для кэша результатов NSI-поиска.
# Полный ключ: f'{_NSI_RESULTS_KEY_PREFIX}{message_id}'
_NSI_RESULTS_KEY_PREFIX = 'nsi_results_'
# Сколько результатов показываем и сохраняем в кэш (для кнопок).
_NSI_TOP_K = 5
# Минимальный similarity, ниже которого результат скрывается.
_NSI_MIN_SIMILARITY = 50.0
# Порог «слабого» SQL-совпадения. Если MAX(similarity) в SQL ниже этого —
# параллельно показываем и NSI, даже если SQL что-то нашёл.
# (Защита от случая «SQL нашёл мусор, NSI нашёл точно».)
_SQL_WEAK_THRESHOLD = 75.0


def _build_nsi_results_key(message_id: int) -> str:
    return f'{_NSI_RESULTS_KEY_PREFIX}{message_id}'


def _split_nsi_query(query: str) -> dict:
    """Эвристика: куда отдать запрос — в `article` или в `name`.

    - Если в строке есть цифры и длина <= 30 — считаем артикулом (иначе имя).
    - Если несколько токенов через пробел и есть цифры — артикул.
    - Длинный текст без цифр — имя.
    FastAPI делает гибридный поиск, поэтому передача в `name` тоже
    сработает через word/vector match.
    """
    q = query.strip()
    if not q:
        return {'name': q}
    has_digit = bool(re.search(r'\d', q))
    short = len(q) <= 30
    if has_digit and short and ' ' not in q:
        return {'article': q}
    return {'name': q}


def _sql_search(query: str) -> list[dict]:
    """Поиск в Total_Price по article/code/description/vendor.

    Возвращает нормализованный список в формате NSI-результатов,
    чтобы downstream-код (форматирование, callback) был общий.

    Стратегия: exact > prefix > contains, top 5, отсортированы
    по убыванию similarity.

    Совместимо с SQLite (дев) и MSSQL (прод) — разная форма запроса
    через `is_sqlite`, как в остальном SqlRepository.
    """
    q = query.strip()
    if not q:
        return []

    from sqlalchemy import text
    like_pct = f'%{q}%'
    like_prefix = f'{q}%'

    def _do_query():
        repository = SqlRepository(settings.DATABASE_URL)
        nolock = repository._nolock
        if repository.is_sqlite:
            # SQLite: LIMIT вместо TOP, без NOLOCK
            sql = text(f"""
                SELECT Vendor, Part_Num, Descr, Price, Units, ArticlePC
                FROM Total_Price
                WHERE
                    Part_Num = :exact
                    OR ArticlePC = :exact
                    OR Part_Num LIKE :prefix ESCAPE '\\'
                    OR Vendor LIKE :pct ESCAPE '\\'
                    OR Descr LIKE :pct ESCAPE '\\'
                LIMIT 10
            """)
        else:
            sql = text(f"""
                SELECT TOP 10
                    Vendor, Part_Num, Descr, Price, Units, ArticlePC
                FROM Total_Price {nolock}
                WHERE
                    Part_Num = :exact
                    OR ArticlePC = :exact
                    OR Part_Num LIKE :prefix ESCAPE '\\'
                    OR Vendor LIKE :pct ESCAPE '\\'
                    OR Descr LIKE :pct ESCAPE '\\'
            """)
        with repository.SessionLocal() as session:
            return session.execute(sql, {
                'exact': q,
                'prefix': like_prefix,
                'pct': like_pct,
            }).fetchall()

    try:
        # Эта функция sync; вызывающий код обязан обернуть в run_in_executor
        rows = _do_query()
    except Exception as e:
        logger.error("[NSI/SQL] search error: %s", e, exc_info=True)
        return []

    q_lower = q.lower()
    results: list[dict] = []
    for row in rows:
        vendor = (row[0] or '').strip()
        article = (row[1] or '').strip()
        name = (row[2] or '').strip()
        price = row[3]
        unit = (row[4] or '').strip() or 'шт'
        code = (row[5] or '').strip()

        # Ранжирование
        art_l = article.lower()
        code_l = code.lower()
        vendor_l = vendor.lower()
        if art_l == q_lower or code_l == q_lower:
            sim, match_type = 100.0, 'exact'
        elif art_l.startswith(q_lower):
            sim, match_type = 85.0, 'prefix'
        elif q_lower in vendor_l:
            sim, match_type = 70.0, 'vendor'
        else:
            sim, match_type = 50.0, 'contains'

        results.append({
            'source': 'sql',
            'vendor': vendor,
            'article': article,
            'name': name,
            'unit': unit,
            'code': code,
            'price': price,
            'similarity_percent': sim,
            'match_type': match_type,
        })

    results.sort(key=lambda r: (-r['similarity_percent'], r['article']))
    return results[:_NSI_TOP_K]


async def _nsi_search(query: str) -> list[dict]:
    """Прямой вызов FastAPI engineering-assistant: GET /search.

    Возвращает список сырых результатов из JSON-ответа (results[].results[]).
    Пустой список при ошибке.
    """
    base = settings.NSI_API_URL.rstrip('/')
    params = {**_split_nsi_query(query), 'top_k': _NSI_TOP_K}
    url = f'{base}/search'
    logger.info("[NSI] GET %s params=%s", url, params)
    try:
        async with httpx.AsyncClient(timeout=settings.NSI_REQUEST_TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        logger.error("[NSI] timeout: query=%r", query)
        return []
    except httpx.HTTPError as e:
        logger.error("[NSI] http error: %s", e)
        return []
    except Exception as e:
        logger.error("[NSI] unexpected: %s", e, exc_info=True)
        return []

    results = (data or {}).get('results') or []
    # Фильтруем по similarity и проставляем source='nsi'
    return [
        {**r, 'source': 'nsi'}
        for r in results
        if float(r.get('similarity_percent') or 0) >= _NSI_MIN_SIMILARITY
    ]


def _format_nsi_results(results: list[dict], query: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Формирует текст сообщения и инлайн-клавиатуру для результатов поиска.

    Поддерживает смешанные результаты: SQL + NSI в одном сообщении.
    Рендерит секции по источникам (🗄 Total_Price / 🔮 NSI).

    Различаем:
      - source='sql' — позиция уже в Total_Price. Кнопка «Добавить» НЕ нужна.
        Показываем инфо + цену.
      - source='nsi' — позиция из 1С-номенклатуры, но в нашей SQL её нет.
        Кнопка «Добавить» нужна.

    Возвращает (text, keyboard). Keyboard=None, если нет результатов.
    """
    if not results:
        return (
            f"🔍 <b>Поиск:</b> <i>{query}</i>\n\n"
            "Ничего не найдено ни в Total_Price, ни в NSI. Попробуйте:\n"
            "• другой артикул или часть слова\n"
            "• вендор (например, <code>ABB</code>)\n"
            "• наименование (<code>автомат 16А</code>)",
            None,
        )

    # Группируем по источнику, сохраняя порядок элементов (для индексов кнопок)
    sql_items: list[tuple[int, dict]] = []
    nsi_items: list[tuple[int, dict]] = []
    for idx, r in enumerate(results):
        src = r.get('source', 'nsi')
        if src == 'sql':
            sql_items.append((idx, r))
        else:
            nsi_items.append((idx, r))

    total = len(results)
    lines = [f"🔍 <b>Поиск:</b> <i>{query}</i> · найдено: <b>{total}</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []

    def _render_item(r: dict) -> str:
        sim = r.get('similarity_percent', 0)
        sim_emoji = '🟢' if sim >= 90 else '🟡' if sim >= 75 else '🟠'
        vendor = (r.get('vendor') or '—').strip()
        article = (r.get('article') or '—').strip()
        name = (r.get('name') or '').strip()
        code = (r.get('code') or '').strip() or '—'
        unit = (r.get('unit') or '').strip()
        if len(name) > 80:
            name = name[:77] + '…'
        unit_str = f" · <i>{unit}</i>" if unit else ''
        if code and code != '—':
            code_line = f"    Код 1С: <code>{code}</code> · "
        else:
            code_line = "    Код 1С: <i>—</i> · "
        extra = ''
        if r.get('source') == 'sql':
            price = r.get('price')
            if price is not None:
                try:
                    price_f = float(price)
                    if price_f > 0:
                        extra = f"\n    💰 <b>{price_f:,.2f} ₽</b>"
                    else:
                        extra = f"\n    💰 <i>«Цена по запросу»</i>"
                except (ValueError, TypeError):
                    pass
        return (
            f"{sim_emoji} <b>{vendor} | {article}</b>\n"
            f"    {name}{unit_str}\n"
            f"{code_line}совпадение: <b>{sim:.0f}%</b>{extra}"
        )

    # Секция SQL
    if sql_items:
        lines.append(f"🗄 <b>Total_Price</b> ({len(sql_items)}) — уже в нашей БД:")
        for _, r in sql_items:
            lines.append(_render_item(r))
        lines.append("")

    # Секция NSI
    if nsi_items:
        lines.append(f"🔮 <b>NSI</b> (engineering-assistant) ({len(nsi_items)}) — нажмите «Добавить», чтобы внести в прайс:")
        for idx, r in nsi_items:
            lines.append(_render_item(r))
            vendor = (r.get('vendor') or '—').strip()
            article = (r.get('article') or '—').strip()
            buttons.append([
                InlineKeyboardButton(
                    f"➕ #{idx + 1} · {vendor[:14]} | {article[:18]}",
                    callback_data=f'nsi_add_{idx}',
                ),
            ])
        lines.append("")

    # Кнопка «Закрыть» — для любого непустого результата
    buttons.append([InlineKeyboardButton("🚫 Закрыть", callback_data="nsi_close")])
    return "\n".join(lines).rstrip(), InlineKeyboardMarkup(buttons)


@admin_only
async def nsi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /nsi <запрос> — поиск номенклатуры в векторной БД 1С.

    Примеры:
        /nsi TJ-15           — поиск по артикулу
        /nsi автомат 16А     — поиск по наименованию (гибрид)
        /nsi ABB S203-C16    — гибридный поиск

    Под каждым результатом кнопка «Добавить» — INSERT в Total_Price
    с Price=0 и PriceText='Цена по запросу'.
    """
    if not update.message or not update.message.text:
        return
    query = update.message.text.split(maxsplit=1)
    if len(query) < 2 or not query[1].strip():
        await update.message.reply_text(
            "🔍 <b>NSI поиск</b>\n\n"
            "Использование: <code>/nsi &lt;запрос&gt;</code>\n\n"
            "Примеры:\n"
            "• <code>/nsi TJ-15</code> — по артикулу\n"
            "• <code>/nsi автомат 16А</code> — по наименованию (гибрид)\n"
            "• <code>/nsi ABB S203-C16</code> — гибрид",
            parse_mode='HTML',
        )
        return

    q = query[1].strip()
    if not settings.NSI_API_URL:
        await update.message.reply_text(
            "❌ NSI_API_URL не настроен в .env\n"
            "Укажите: <code>NSI_API_URL=http://192.168.10.19:8000/api/nsi</code>",
            parse_mode='HTML',
        )
        return

    msg = await update.message.reply_text(f"🔍 Ищу <i>{q}</i> в Total_Price + NSI…", parse_mode='HTML')

    # Параллельно: SQL (через executor) + NSI (HTTP). Оба быстрые.
    sql_task = asyncio.get_event_loop().run_in_executor(None, _sql_search, q)
    nsi_task = _nsi_search(q)
    sql_results, nsi_results = await asyncio.gather(sql_task, nsi_task)

    # Логика отображения:
    #   - SQL нашёл ХОРОШИЕ совпадения (max sim >= _SQL_WEAK_THRESHOLD) → только SQL.
    #   - SQL пуст или слаб → показываем оба источника.
    sql_max = max((r.get('similarity_percent', 0) for r in sql_results), default=0)
    sql_strong = bool(sql_results) and sql_max >= _SQL_WEAK_THRESHOLD

    if sql_strong and not nsi_results:
        results = sql_results
        sql_shown, nsi_shown = len(sql_results), 0
    elif sql_strong and nsi_results:
        # Оба есть, но SQL сильный — всё равно покажем оба, чтобы пользователь
        # видел и «наше», и «из 1С». Это редкий случай (запрос совпадает в обеих БД).
        results = sql_results + nsi_results
        sql_shown, nsi_shown = len(sql_results), len(nsi_results)
    else:
        # SQL пуст или слаб — показываем всё, что нашли.
        results = sql_results + nsi_results
        sql_shown, nsi_shown = len(sql_results), len(nsi_results)

    text, keyboard = _format_nsi_results(results, q)
    reply_markup = keyboard

    # Кэшируем результаты в user_data, чтобы callback мог их достать.
    # Ключ = message_id, чтобы разные поиски не пересекались.
    context.user_data[_build_nsi_results_key(msg.message_id)] = results

    await msg.edit_text(text, parse_mode='HTML', reply_markup=reply_markup)
    logger.info(
        "[NSI] query=%r sql=%d (max_sim=%.1f) nsi=%d shown=%d (sql=%d, nsi=%d) strong=%s",
        q, len(sql_results), sql_max, len(nsi_results),
        len(results), sql_shown, nsi_shown, sql_strong,
    )


async def _nsi_add_callback(query, context: ContextTypes.DEFAULT_TYPE, result_idx: int):
    """Обрабатывает нажатие «➕ Добавить» под результатом NSI-поиска."""
    message_id = query.message.message_id
    key = _build_nsi_results_key(message_id)
    results = context.user_data.get(key) or []
    if result_idx < 0 or result_idx >= len(results):
        await query.answer("⚠️ Результат устарел. Выполните поиск заново.", show_alert=True)
        return

    item = results[result_idx]
    vendor = (item.get('vendor') or '').strip()
    article = (item.get('article') or '').strip()
    code = (item.get('code') or '').strip()
    name = (item.get('name') or '').strip()
    unit = (item.get('unit') or '').strip() or 'шт'
    source = item.get('source', 'nsi')
    source_badge = '🗄 SQL' if source == 'sql' else '🔮 NSI'

    if not vendor or not article:
        await query.answer("⚠️ Нет vendor/article в результате.", show_alert=True)
        return

    # Защита: кнопка «Добавить» показывается только для NSI-результатов.
    # Если callback каким-то образом сработал на SQL-результате (например,
    # устаревшее сообщение с кнопкой) — не дублируем, а сообщаем.
    if source == 'sql':
        await query.answer(
            "ℹ️ Эта позиция уже в Total_Price — добавлять нечего.",
            show_alert=True,
        )
        return

    def _do_add():
        repository = SqlRepository(settings.DATABASE_URL)
        if repository.find_by_vendor_part_num(vendor, article):
            return 'exists', None
        price_item = PriceItem(
            vendor=vendor,
            article=article,
            description=name,
            price=Decimal(0),  # → PriceText='Цена по запросу' в add_items
            units=unit,
            storage='',
            code_1c=code,
        )
        added = repository.add_items([price_item])
        return 'added', added

    status, info = await asyncio.get_event_loop().run_in_executor(None, _do_add)

    sim = item.get('similarity_percent', 0)
    code_line = f"Код 1С: <code>{code}</code>\n" if code else "Код 1С: <i>—</i>\n"
    if status == 'exists':
        text = (
            f"⚠️ <b>Уже в БД</b> · источник: {source_badge}\n\n"
            f"<b>{vendor} | {article}</b>\n"
            f"{code_line}"
            f"Совпадение: {sim:.0f}%\n\n"
            f"Позиция уже есть в Total_Price, дубликат не создан."
        )
    else:
        text = (
            f"✅ <b>Добавлено в прайс</b> · источник: {source_badge}\n\n"
            f"<b>{vendor} | {article}</b>\n"
            f"Наименование: {name}\n"
            f"{code_line}"
            f"Ед.: {unit}\n"
            f"Цена: <i>«Цена по запросу»</i> (Price=0)\n"
            f"Совпадение: {sim:.0f}%"
        )
    await query.edit_message_text(text, parse_mode='HTML')
    # Кэш больше не нужен — кнопка отработала.
    context.user_data.pop(key, None)
    logger.info(
        "[NSI] add: source=%s vendor=%s article=%s status=%s similarity=%.1f",
        source, vendor, article, status, sim,
    )


# ─── Загрузка прайса через файл ───────────────────────────────────────────────

# Карта ключевых слов в имени файла → имя вендора в реестре
_FILENAME_VENDOR_MAP = {
    'akel':  'AKEL',
    'shina': 'ШИНА',
    'меко':  'МЕКО',
    'ао сэ': 'SE',
    'sahat': 'SELECTRIC',
    'generic': 'GENERIC',  # универсальный парсер: ищет колонки по алиасам
}

# Вендоры, которые принимают файл через бота (используется в подсказке)
_UPLOAD_VENDORS = list(_FILENAME_VENDOR_MAP.values())

_MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB — ограничение Telegram Bot API


def detect_vendor_from_filename(filename: str) -> str:
    """Определяет вендора по ключевым словам в имени файла.

    Если ни одно ключевое слово не нашлось — возвращает 'GENERIC'.
    Это безопасный фолбэк: generic-парсер сам упадёт, если в файле
    не окажется обязательных колонок (article + price), и пользователь
    увидит понятную ошибку.
    """
    lower = filename.lower()
    for keyword, vendor in _FILENAME_VENDOR_MAP.items():
        if keyword in lower:
            return vendor
    return 'GENERIC'


async def _handle_shina_upload(update: Update, file_path):
    """Загружает конфигурацию шин из Excel в shina_config и пересчитывает Total_Price."""
    from adapters.parsers.shina_parser import ShinaParser
    from domain.services.shina_service import ShinaService

    msg = await update.message.reply_text("🔄 Загружаю конфигурацию шин...")
    try:
        repository = SqlRepository(settings.DATABASE_URL)
        service = ShinaService(repository, ShinaParser())

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, service.load_from_excel, file_path)

        await msg.edit_text(
            f"✅ <b>ШИНА</b> — конфигурация загружена!\n\n"
            f"📦 Позиций в конфиге: {result['loaded']}\n"
            f"🔄 Обновлено в Total_Price: {result['updated']}\n\n"
            f"🔴 Медь: {result['copper_price_per_kg']} руб/кг\n"
            f"⚪ Алюминий: {result['alum_price_per_kg']} руб/кг",
            parse_mode='HTML',
        )
        logger.info("[SHINA] upload: loaded=%d updated=%d",
                    result['loaded'], result['updated'])
    except Exception as e:
        logger.error("[SHINA] _handle_shina_upload: %s", e, exc_info=True)
        await msg.edit_text(f"❌ Ошибка загрузки ШИНА:\n{str(e)[:300]}")


async def _handle_sahat_upload(update: Update, file_path):
    """Загружает конфигурацию Selectric из Excel в selectric_mccb_config / selectric_acb_config."""
    from adapters.parsers.sahat_parser import SahatParser
    from domain.services.sahat_service import SahatService

    msg = await update.message.reply_text("🔄 Загружаю конфигурацию Selectric (Sahat DDP)...")
    try:
        repository = SqlRepository(settings.DATABASE_URL)
        service = SahatService(repository, SahatParser())

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, service.load_from_excel, file_path)

        await msg.edit_text(
            f"✅ <b>SELECTRIC</b> — конфигурация загружена!\n\n"
            f"🔌 MCCB (литой корпус): {result['mccb_loaded']} позиций\n"
            f"💨 ACB (воздушные): {result['acb_loaded']} позиций",
            parse_mode='HTML',
        )
        logger.info("[SELECTRIC] upload: mccb=%d acb=%d", result['mccb_loaded'], result['acb_loaded'])
    except Exception as e:
        logger.error("[SELECTRIC] _handle_sahat_upload: %s", e, exc_info=True)
        await msg.edit_text(f"❌ Ошибка загрузки SELECTRIC:\n{str(e)[:300]}")


@admin_only
async def upload_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входящего документа — загружает прайс вендора из файла.

    Администратор отправляет Excel-файл боту. Бот определяет тип файла:
      1. discontinued — по имени файла (legacy).
      2. mapping 1C — по ЗАГОЛОВКАМ колонок (Артикул + Код + Производитель).
      3. price-list вендора — по имени файла (ключевые слова) с фолбэком GENERIC.
    """
    doc = update.message.document
    if doc is None:
        return

    filename = doc.file_name or ''
    logger.info("[FIX] upload_price_handler: filename=%s size=%s", filename, doc.file_size)

    # Проверяем размер файла
    if doc.file_size and doc.file_size > _MAX_UPLOAD_SIZE:
        await update.message.reply_text(
            f"⚠️ Файл слишком большой ({doc.file_size // (1024*1024)} МБ).\n"
            f"Telegram Bot API не позволяет скачивать файлы > 20 МБ."
        )
        return

    # 1) Снятые с производства — по имени файла (legacy).
    is_discontinued = any(kw in filename.lower() for kw in ('снят', 'disc'))
    if is_discontinued:
        settings.PRICE_FILES_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = settings.PRICE_FILES_DIR / filename
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(dest_path))
        await _handle_discontinued_upload(update, dest_path)
        return

    # Скачиваем файл один раз — дальше используем и для маппинга, и для вендора.
    settings.PRICE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = settings.PRICE_FILES_DIR / filename
    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(str(dest_path))
    logger.info("[FIX] upload_price_handler: файл сохранён path=%s", dest_path)

    # 2) Маппинг 1С — по ЗАГОЛОВКАМ колонок (имя файла не важно).
    if _looks_like_mapping_file(dest_path):
        await _handle_mapping_upload(update, dest_path, context)
        return

    # 3) Определяем вендора по имени файла (с фолбэком на GENERIC).
    vendor = detect_vendor_from_filename(filename)

    await update.message.reply_text(f"📥 Получен файл <b>{filename}</b>\nВендор: <b>{vendor}</b>\nНачинаю синхронизацию...", parse_mode='HTML')

    try:
        # Специальный путь для ШИНА
        if vendor == 'ШИНА':
            await _handle_shina_upload(update, dest_path)
            return

        # Специальный путь для SELECTRIC (Sahat DDP)
        if vendor == 'SELECTRIC':
            await _handle_sahat_upload(update, dest_path)
            return

        # Создаём сервис синхронизации с UploadDownloader
        normalizer = ArticleNormalizer()
        registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
        repository = SqlRepository(settings.DATABASE_URL)
        report_service = ReportService(settings.PROJECT_ROOT / "reports")

        downloader = UploadDownloader(dest_path)
        parser = registry.create_parser(vendor)

        service = SyncService(
            downloader=downloader,
            parser=parser,
            repository=repository,
            price_change_threshold=settings.PRICE_CHANGE_THRESHOLD,
            report_service=report_service,
        )

        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: service.sync_vendor(vendor, mark_disappeared=False)
        )
        # sync_vendor мог подменить vendor на реальный (из колонки «Производитель»),
        # если файл generic и в нём один вендор. Берём из result.vendor, чтобы
        # в Telegram-отчёте было видно правильное имя.
        actual_vendor = getattr(result, 'vendor', vendor) or vendor
        logger.info("[FIX] upload_price_handler: sync done vendor=%s (file=%s) total=%s new=%s updated=%s",
                    actual_vendor, vendor, result.total_items, result.new_items, result.updated_items)

        report_lines = [
            f"✅ <b>{actual_vendor}</b> — синхронизация завершена!",
            f"",
            f"📦 Всего позиций: {result.total_items}",
            f"➕ Новых: {result.new_items}",
            f"🔄 Обновлено: {result.updated_items}",
        ]
        if result.restored_items > 0:
            report_lines.append(f"♻️ Восстановлено: {result.restored_items}")
        if result.price_changes_count > 0:
            report_lines.append(f"💰 Изменений цен: {result.price_changes_count}")
        if result.disappeared_items > 0:
            report_lines.append(f"👻 Исчезло: {result.disappeared_items}")

        last_stats = getattr(parser, '_last_stats', None)
        if last_stats and last_stats.get('total', 0) > 0:
            report_lines.append(
                f"📋 Листов в книге: {last_stats['total']}, "
                f"с заголовками: {last_stats['with_headers']}"
            )

        await update.message.reply_text("\n".join(report_lines), parse_mode='HTML')

    except Exception as e:
        logger.error("[FIX] upload_price_handler: ошибка vendor=%s: %s", vendor, e, exc_info=True)
        await update.message.reply_text(f"❌ Ошибка при синхронизации <b>{vendor}</b>:\n{str(e)[:300]}", parse_mode='HTML')


async def _handle_discontinued_upload(update: Update, dest_path):
    """Импорт списка снятых с производства из Excel/CSV файла.

    Формат файла: строки с колонками Vendor | Article | Replacement (необязательно).
    Первая строка пропускается если не является данными (заголовок).
    """
    import openpyxl
    import csv as csv_mod

    ext = dest_path.suffix.lower()
    raw_rows = []
    try:
        if ext in ('.xlsx', '.xls'):
            wb = openpyxl.load_workbook(dest_path, read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                if any(c is not None for c in row):
                    raw_rows.append([str(c).strip() if c is not None else '' for c in row])
            wb.close()
        elif ext == '.csv':
            with open(dest_path, 'r', encoding='utf-8-sig') as f:
                for row in csv_mod.reader(f):
                    if row:
                        raw_rows.append([c.strip() for c in row])
        else:
            await update.message.reply_text("⚠️ Поддерживаются .xlsx и .csv файлы.")
            return
    except Exception as e:
        logger.error("[DISCONTINUED] upload parse error: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Ошибка чтения файла: {e}")
        return

    if not raw_rows:
        await update.message.reply_text("⚠️ Файл пустой.")
        return

    known_vendors = {'KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT'}
    if raw_rows[0][0].upper() not in known_vendors:
        raw_rows = raw_rows[1:]  # пропускаем заголовок

    if not raw_rows:
        await update.message.reply_text("⚠️ В файле нет данных (только заголовок?).")
        return

    await update.message.reply_text(f"📥 Начинаю импорт {len(raw_rows)} позиций...")

    def _do_batch():
        repo = SqlRepository(settings.DATABASE_URL)
        marked = 0
        not_found = []
        for row in raw_rows:
            if len(row) < 2 or not row[0] or not row[1]:
                continue
            vendor = row[0].upper()
            article = row[1]
            replacement = row[2] if len(row) > 2 and row[2] else None
            try:
                ok = repo.mark_as_discontinued(vendor, article, replacement)
                if ok:
                    marked += 1
                else:
                    not_found.append(f"{vendor} | {article}")
            except Exception as e:
                logger.error("[DISCONTINUED] batch row error %s %s: %s", vendor, article, e)
                not_found.append(f"{vendor} | {article} (ERR)")
        return marked, not_found

    marked, not_found = await asyncio.get_event_loop().run_in_executor(None, _do_batch)

    lines = [f"✅ <b>Импорт завершён</b>: помечено <b>{marked}</b> позиций."]
    if not_found:
        lines.append(f"⚠️ Не найдено в БД: {len(not_found)}")
        for ex in not_found[:10]:
            lines.append(f"  • {ex}")
        if len(not_found) > 10:
            lines.append(f"  ... и ещё {len(not_found) - 10}")
    logger.info("[DISCONTINUED] batch upload: marked=%d not_found=%d file=%s",
                marked, len(not_found), dest_path.name)
    await update.message.reply_text("\n".join(lines), parse_mode='HTML',
                                    reply_markup=_disc_main_keyboard())


# ─── Снятые с производства (/discontinued) ────────────────────────────────────

_DISC_VENDORS = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']
_DISC_PAGE_SIZE = 20


def _disc_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Список снятых", callback_data="disc_list_menu")],
        [InlineKeyboardButton("✅ Пометить как снятый", callback_data="disc_mark_start")],
        [InlineKeyboardButton("↩️ Снять пометку", callback_data="disc_unmark_start")],
    ])


def _disc_vendor_keyboard(prefix: str):
    buttons = [
        [InlineKeyboardButton(v, callback_data=f"{prefix}{v}")]
        for v in _DISC_VENDORS
    ]
    buttons.append([InlineKeyboardButton("ВСЕ", callback_data=f"{prefix}ALL")])
    return InlineKeyboardMarkup(buttons)


@admin_only
async def discontinued_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /discontinued — управление снятыми с производства позициями."""
    logger.info("[DISCONTINUED] /discontinued: user=%s", update.effective_user.id)
    await update.message.reply_text(
        "📵 <b>Снятые с производства</b>\n\nВыберите действие:",
        parse_mode='HTML',
        reply_markup=_disc_main_keyboard(),
    )


async def _handle_disc_mark_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовый ввод для пометки позиции как discontinued."""
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ Укажите: VENDOR ARTICLE [REPLACEMENT]\nПример: KEAZ ABC123 XYZ456"
        )
        return
    vendor, article = parts[0].upper(), parts[1]
    replacement = parts[2] if len(parts) >= 3 else None
    context.user_data.pop('disc_state', None)
    try:
        repository = SqlRepository(settings.DATABASE_URL)
        ok = repository.mark_as_discontinued(vendor, article, replacement)
        if ok:
            msg = f"✅ <b>{vendor} | {article}</b> помечен как снятый с производства."
            if replacement:
                msg += f"\n🔄 Замена: {replacement}"
        else:
            msg = f"⚠️ Позиция <b>{vendor} | {article}</b> не найдена в БД."
        logger.info("[DISCONTINUED] mark via TG: vendor=%s article=%s replacement=%s ok=%s",
                    vendor, article, replacement, ok)
    except Exception as e:
        logger.error("[DISCONTINUED] _handle_disc_mark_input: %s", e, exc_info=True)
        msg = f"❌ Ошибка: {str(e)[:200]}"
    await update.message.reply_text(msg, parse_mode='HTML', reply_markup=_disc_main_keyboard())


async def _handle_disc_unmark_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовый ввод для снятия пометки discontinued."""
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ Укажите: VENDOR ARTICLE\nПример: KEAZ ABC123"
        )
        return
    vendor, article = parts[0].upper(), parts[1]
    context.user_data.pop('disc_state', None)
    try:
        repository = SqlRepository(settings.DATABASE_URL)
        ok = repository.unmark_discontinued(vendor, article)
        msg = (
            f"✅ <b>{vendor} | {article}</b> — пометка снята, статус active."
            if ok else
            f"⚠️ Позиция <b>{vendor} | {article}</b> не найдена или уже не discontinued."
        )
        logger.info("[DISCONTINUED] unmark via TG: vendor=%s article=%s ok=%s", vendor, article, ok)
    except Exception as e:
        logger.error("[DISCONTINUED] _handle_disc_unmark_input: %s", e, exc_info=True)
        msg = f"❌ Ошибка: {str(e)[:200]}"
    await update.message.reply_text(msg, parse_mode='HTML', reply_markup=_disc_main_keyboard())


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений — обслуживает состояния диалогов."""
    admin_ids = settings.ADMIN_IDS
    if admin_ids and update.effective_user.id not in admin_ids:
        return

    disc_state = context.user_data.get('disc_state')
    if disc_state == 'awaiting_mark':
        await _handle_disc_mark_input(update, context)
        return
    if disc_state == 'awaiting_unmark':
        await _handle_disc_unmark_input(update, context)
        return