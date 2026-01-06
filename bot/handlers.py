import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
import subprocess
from pathlib import Path
import re
from config.settings import settings



logger = logging.getLogger(__name__)

PROJECT_ROOT = settings.PROJECT_ROOT

sync_status = {
    'is_running': False,
    'current_vendor': None,
    'last_results': {}
}


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user

    text = f"""🤖 Привет, {user.first_name}!

Команды:

/sync - Синхронизировать вендора
/sync_all - Синхронизировать всех
/status - Статус
/debug - Показать ошибки
/help - Справка"""

    await update.message.reply_text(text)


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /sync"""
    vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT']

    keyboard = [[InlineKeyboardButton(f"🔄 {v}", callback_data=f"sync_{v}")] for v in vendors]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("📋 Выберите вендора:", reply_markup=reply_markup)


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать последние ошибки"""
    debug_info = []
    
    # Проверяем last_error
    if sync_status.get('last_error'):
        debug_info.append(f"🔍 Last Error:\n{sync_status['last_error'][:800]}")
    
    # Показываем stdout и stderr последнего запуска
    if sync_status.get('last_stdout'):
        debug_info.append(f"\n📤 STDOUT:\n{sync_status['last_stdout'][-800:]}")
    
    if sync_status.get('last_stderr'):
        debug_info.append(f"\n📥 STDERR:\n{sync_status['last_stderr'][-800:]}")
    
    # Показываем return code
    if sync_status.get('last_returncode') is not None:
        debug_info.append(f"\n🔢 Return Code: {sync_status['last_returncode']}")
    
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

    vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT']

    for i, vendor in enumerate(vendors, 1):
        sync_status['current_vendor'] = vendor

        await msg.edit_text(f"🔄 {i}/{len(vendors)}: {vendor}")

        try:
            result = subprocess.run(
                ['python', 'main.py', vendor],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(settings.PROJECT_ROOT)  # Используем из settings
            )

            sync_status['last_stdout'] = result.stdout
            sync_status['last_stderr'] = result.stderr
            sync_status['last_returncode'] = result.returncode

            if result.returncode == 0:
                output = result.stdout + result.stderr
                
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
    vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT']
    text = "📋 Вендоры:\n\n" + "\n".join(f"{i}. {v}" for i, v in enumerate(vendors, 1))
    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    text = """📚 Команды:

/sync - Синхронизировать вендора
/sync_all - Синхронизировать всех
/status - Статус синхронизаций
/debug - Показать ошибки
/help - Справка"""

    await update.message.reply_text(text)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()

    if query.data.startswith('sync_'):
        vendor = query.data.replace('sync_', '')

        if sync_status['is_running']:
            await query.edit_message_text(f"⚠️ Идет: {sync_status['current_vendor']}")
            return

        sync_status['is_running'] = True
        sync_status['current_vendor'] = vendor

        await query.edit_message_text(f"🚀 Синхронизация {vendor}...")

        try:
            result = subprocess.run(
                ['python', 'main.py', vendor],
                capture_output=True,
                text=True,
                timeout=300,
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