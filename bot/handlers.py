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



logger = logging.getLogger(__name__)

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
/status - Статус синхронизаций
/debug - Показать ошибки
/help - Справка

💡 Используйте /check чтобы посмотреть какие изменения будут при синхронизации"""

    await update.message.reply_text(text)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()

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