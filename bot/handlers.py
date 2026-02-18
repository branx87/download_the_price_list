import asyncio
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
from domain.services.report_service import ReportService



logger = logging.getLogger(__name__)

# Хранилище последнего ErpSyncResult для генерации отчёта по кнопке
_last_erp_result = {
    'result': None,
}

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

    text = f"""🤖 Привет, {user.first_name}!

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
/db_copy - Скопировать БД из MSSQL
/status - Статус
/debug - Показать ошибки
/help - Справка"""

    await update.message.reply_text(text)


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /sync"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']

    keyboard = [[InlineKeyboardButton(f"🔄 {v}", callback_data=f"sync_{v}")] for v in vendors]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("📋 Выберите вендора для синхронизации:", reply_markup=reply_markup)


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /check - проверка актуальности прайсов"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']

    keyboard = [[InlineKeyboardButton(f"🔍 {v}", callback_data=f"check_{v}")] for v in vendors]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("📋 Выберите вендора для проверки:", reply_markup=reply_markup)


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
    """Парсит вывод main.py и извлекает статистику"""
    total = new = disappeared = 0
    
    if not output:  # ← ДОБАВЬ ПРОВЕРКУ
        logger.warning(f"{vendor}: output is empty")
        return 0, 0, 0
    
    # Ищем строку типа: "KEAZ: total=31986, new=0, updated=0, disappeared=0, time=5.0s"
    pattern = rf"{vendor}:\s*total=(\d+),\s*new=(\d+),\s*updated=\d+,\s*disappeared=(\d+)"
    
    for line in output.split('\n'):
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            total = int(match.group(1))
            new = int(match.group(2))
            disappeared = int(match.group(3))
            break
    
    return total, new, disappeared


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
                
                # # Добавь логирование полного вывода для DKC
                # if vendor == 'DKC':
                #     logger.info(f"DKC stdout: {result.stdout}")
                #     logger.error(f"DKC stderr: {result.stderr}")
                
                # total, new, disappeared = parse_sync_output(output, vendor)

                total, new, disappeared = parse_sync_output(output, vendor)

                sync_status['last_results'][vendor] = {
                    'success': True,
                    'total': total,
                    'new': new,
                    'disappeared': disappeared
                }

                # Проверяем успешность по наличию галочки в выводе
                success = '✅' in output or f'{vendor}:' in output

                if success and total > 0:
                    report = f"""✅ {vendor}
📦 Всего: {total}
➕ Новых: {new}
👻 Исчезло: {disappeared}"""
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
            text += f"  Исчезло: {result.get('disappeared', 0)}\n"
        text += "\n"

    await update.message.reply_text(text)


async def vendors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /vendors"""
    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']
    text = "📋 Вендоры:\n\n" + "\n".join(f"{i}. {v}" for i, v in enumerate(vendors, 1))
    await update.message.reply_text(text)


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
/db_copy - Скопировать БД из MSSQL
/status - Статус синхронизаций
/debug - Показать ошибки
/help - Справка

💡 Используйте /check чтобы посмотреть какие изменения будут при синхронизации"""

    await update.message.reply_text(text)


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
    await query.answer()

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
            
            # Логируем для отладки
            if vendor == 'DKC':
                logger.info(f"DKC stdout length: {len(stdout)}")
                logger.info(f"DKC stderr length: {len(stderr)}")
                logger.info(f"DKC returncode: {result.returncode}")
                logger.error(f"DKC STDERR CONTENT:\n{stderr}")
                logger.error(f"DKC STDOUT LAST 2000 chars:\n{stdout[-2000:]}")  # ← ДОБАВЬ ЭТО
            
            if result.returncode == 0:
                output = stdout + stderr
                
                total, new, disappeared = parse_sync_output(output, vendor)
                
                sync_status['last_results'][vendor] = {
                    'success': True,
                    'total': total,
                    'new': new,
                    'disappeared': disappeared,
                }
                
                success = '✅' in output or f'{vendor}:' in output
                
                if success and total > 0:
                    report = f"""✅ {vendor} готово!

📦 Всего: {total}
➕ Новых: {new}
👻 Исчезло: {disappeared}"""
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