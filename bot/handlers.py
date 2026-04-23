import asyncio
import functools
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
import subprocess
from pathlib import Path
import re
from config.settings import settings
from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository
from domain.services.sync_service import SyncService
from domain.services.report_service import ReportService
from adapters.erp.erp_client import ErpClient
from domain.services.erp_sync_service import ErpSyncService
from adapters.downloaders.upload_downloader import UploadDownloader


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
/db_copy - Скопировать БД из MSSQL
/status - Статус
/debug - Показать ошибки
/help - Справка"""

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
/db_copy - Скопировать БД из MSSQL
/status - Статус синхронизаций
/debug - Показать ошибки
/help - Справка

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


# ─── Загрузка прайса через файл ───────────────────────────────────────────────

# Карта ключевых слов в имени файла → имя вендора в реестре
_FILENAME_VENDOR_MAP = {
    'akel':  'AKEL',
    'shina': 'ШИНА',
}

# Вендоры, которые принимают файл через бота (используется в подсказке)
_UPLOAD_VENDORS = list(_FILENAME_VENDOR_MAP.values())

_MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB — ограничение Telegram Bot API


def detect_vendor_from_filename(filename: str) -> str | None:
    """Определяет вендора по ключевым словам в имени файла."""
    lower = filename.lower()
    for keyword, vendor in _FILENAME_VENDOR_MAP.items():
        if keyword in lower:
            return vendor
    return None


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


@admin_only
async def upload_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входящего документа — загружает прайс вендора из файла.

    Администратор отправляет Excel-файл боту. Бот определяет вендора по имени
    файла, парсит прайс и синхронизирует данные с БД.
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

    # Определяем вендора по имени файла
    vendor = detect_vendor_from_filename(filename)
    if vendor is None:
        vendors_hint = ', '.join(_UPLOAD_VENDORS)
        await update.message.reply_text(
            f"⚠️ Не удалось определить вендора по имени файла: <b>{filename}</b>\n\n"
            f"Поддерживаемые вендоры (загрузка файлом): {vendors_hint}\n"
            f"Имя файла должно содержать ключевое слово вендора.",
            parse_mode='HTML',
        )
        return

    await update.message.reply_text(f"📥 Получен файл <b>{filename}</b>\nВендор: <b>{vendor}</b>\nНачинаю синхронизацию...", parse_mode='HTML')

    try:
        # Скачиваем файл во временную директорию
        settings.PRICE_FILES_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = settings.PRICE_FILES_DIR / filename

        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(dest_path))
        logger.info("[FIX] upload_price_handler: файл сохранён path=%s", dest_path)

        # Специальный путь для ШИНА
        if vendor == 'ШИНА':
            await _handle_shina_upload(update, dest_path)
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

        result = await asyncio.get_event_loop().run_in_executor(None, service.sync_vendor, vendor)
        logger.info("[FIX] upload_price_handler: sync done vendor=%s total=%s new=%s updated=%s",
                    vendor, result.total_items, result.new_items, result.updated_items)

        report_lines = [
            f"✅ <b>{vendor}</b> — синхронизация завершена!",
            f"",
            f"📦 Всего позиций: {result.total_items}",
            f"➕ Новых: {result.new_items}",
            f"🔄 Обновлено: {result.updated_items}",
        ]
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