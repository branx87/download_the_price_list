"""
Bitrix24 bot — обработчики команд.

Доступные команды (текст в чате):
  помощь / /start  → главное меню с кнопками
  статус           → последнее время синхронизации по каждому вендору
  синхронизировать → выбор вендора → запуск синхронизации в фоне
  проверить        → выбор вендора → проверка изменений в фоне
  sync_all         → синхронизировать всех вендоров в фоне

Кнопки отправляют ACTION_VALUE как текст — он и попадает в handle_message().

После завершения фоновых операций результат отправляется в тот же диалог
через BitrixBotAPI.send_message (требует BITRIX_REST_URL + BITRIX_BOT_ID).
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from config.settings import settings
from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository
from domain.services.sync_service import SyncService
from domain.services.report_service import ReportService
from domain.services.data_normalizer import DataNormalizer
from domain.services.shina_service import ShinaService, MATERIAL_ALIASES
from adapters.bitrix.bitrix_api import BitrixBotAPI

logger = logging.getLogger(__name__)

# Вендоры с автоматической загрузкой файла (без ручной загрузки)
AUTO_VENDORS = ['DKC', 'KEAZ', 'ОВЕН', 'EKF', 'IEK', 'CHINT']


# ------------------------------------------------------------------
# Keyboard helpers
# ------------------------------------------------------------------

def _btn(text: str, value: str, bg: str = "#29619b") -> dict:
    return {"TEXT": text, "ACTION": "SEND", "ACTION_VALUE": value,
            "BG_COLOR": bg, "TEXT_COLOR": "#fff"}


def _vendor_kb(prefix: str) -> list:
    """Строит клавиатуру выбора вендора с заданным префиксом команды."""
    rows = []
    for i in range(0, len(AUTO_VENDORS), 3):
        chunk = AUTO_VENDORS[i:i + 3]
        rows.append([_btn(v, f"{prefix}_{v}") for v in chunk])
    rows.append([_btn("🏠 Меню", "помощь", "#555")])
    return rows


KB_MAIN = [
    [_btn("📊 Статус", "статус"), _btn("🔄 Синхронизировать", "синхронизировать")],
    [_btn("🔍 Проверить", "проверить"), _btn("🔄 Все вендоры", "sync_all")],
    [_btn("⚡ Шина", "шина"), _btn("🚫 Снятые", "снятые")],
]

KB_SYNC = _vendor_kb("sync")
KB_CHECK = _vendor_kb("check")
KB_DISC = _vendor_kb("снят")


# ------------------------------------------------------------------
# Message builder
# ------------------------------------------------------------------

def _msg(text: str, keyboard: Optional[list] = None, replace: bool = False) -> dict:
    m: dict = {"text": text}
    if keyboard is not None:
        m["keyboard"] = keyboard
    if replace:
        m["replace"] = True
    return m


# ------------------------------------------------------------------
# Access control
# ------------------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    return user_id in settings.B24_ADMIN_IDS


# ------------------------------------------------------------------
# Service factory
# ------------------------------------------------------------------

def _create_sync_service(vendor: str) -> SyncService:
    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    repo = SqlRepository(settings.DATABASE_URL)
    report_service = ReportService(settings.PROJECT_ROOT / "reports")
    return SyncService(
        downloader=registry.create_downloader(vendor),
        parser=registry.create_parser(vendor),
        repository=repo,
        price_change_threshold=settings.PRICE_CHANGE_THRESHOLD,
        report_service=report_service,
    )


def _get_api() -> BitrixBotAPI:
    return BitrixBotAPI(
        settings.BITRIX_REST_URL,
        settings.BITRIX_BOT_ID,
        php_sender_url=settings.BITRIX_BOT_SENDER_URL,
        php_sender_token=settings.B24_WEBHOOK_TOKEN,
    )


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

async def handle_message(
    dialog_id: str,
    from_user_id: int,
    text: str,
    command: str = "",
    command_params: str = "",
) -> list[dict]:
    is_group = dialog_id.startswith("chat")
    if not _is_admin(from_user_id):
        if is_group:
            return []  # в группе молчим — не засоряем чат
        logger.warning("[B24Bot] Отказ в доступе user_id=%s", from_user_id)
        return [_msg("⛔ Доступ запрещён.")]

    raw = text.strip().lower()

    if raw in ("помощь", "help", "/start", "start", "/help", "меню"):
        return [_msg(_help_text(), keyboard=KB_MAIN)]

    if raw == "статус":
        return await _do_status()

    if raw == "синхронизировать":
        return [_msg("Выберите вендора для синхронизации:", keyboard=KB_SYNC)]

    if raw == "проверить":
        return [_msg("Выберите вендора для проверки:", keyboard=KB_CHECK)]

    if raw == "sync_all":
        asyncio.create_task(_run_sync_all(dialog_id))
        return [_msg("⏳ Запускаю синхронизацию всех вендоров...\nРезультат придёт сюда после завершения.", replace=True)]

    if raw == "шина":
        return await _do_shina()

    if raw.startswith("шина "):
        return await _do_shina_update(raw[5:].strip())

    if raw == "снятые":
        return [_msg(
            "Снятые с производства:\n"
            "• Выберите вендора для просмотра списка\n"
            "• Пометить одну: [B]пометить VENDOR АРТИКУЛ[/B] (с заменой: пометить VENDOR АРТИКУЛ ЗАМЕНА)\n"
            "• Пометить список: [B]пометить-список[/B] (каждая строка: VENDOR АРТИКУЛ ЗАМЕНА)\n"
            "• Снять пометку: [B]восстановить VENDOR АРТИКУЛ[/B]",
            keyboard=KB_DISC,
        )]

    if raw.startswith("снят_"):
        vendor = raw[5:].upper()
        if vendor in AUTO_VENDORS:
            return await _do_disc_list(vendor)

    if raw.startswith("пометить-список"):
        lines = text.strip().split('\n')[1:]
        return await _do_disc_batch(lines)

    if raw.startswith("пометить "):
        return await _do_disc_mark(raw[9:].strip())

    if raw.startswith("восстановить "):
        return await _do_disc_unmark(raw[13:].strip())

    if raw.startswith("sync_"):
        vendor = raw[5:].upper()
        if vendor in AUTO_VENDORS:
            asyncio.create_task(_run_sync_vendor(vendor, dialog_id))
            return [_msg(f"⏳ Запускаю синхронизацию [B]{vendor}[/B]...\nРезультат придёт сюда после завершения.", replace=True)]

    if raw.startswith("check_"):
        vendor = raw[6:].upper()
        if vendor in AUTO_VENDORS:
            asyncio.create_task(_run_check_vendor(vendor, dialog_id))
            return [_msg(f"⏳ Запускаю проверку [B]{vendor}[/B]...\nРезультат придёт сюда после завершения.", replace=True)]

    return [_msg(_help_text(), keyboard=KB_MAIN)]


# ------------------------------------------------------------------
# Status — читаем из БД
# ------------------------------------------------------------------

async def _do_status() -> list[dict]:
    loop = asyncio.get_event_loop()

    def _fetch() -> str:
        repo = SqlRepository(settings.DATABASE_URL)
        stats = repo.get_vendors_status(AUTO_VENDORS)
        lines = ["[B]Статус синхронизации:[/B]\n"]
        for vendor in AUTO_VENDORS:
            key = DataNormalizer.normalize_vendor_name(vendor)
            if key in stats:
                last, count = stats[key]
                if isinstance(last, str):
                    last = datetime.fromisoformat(last)
                lines.append(f"• {vendor}: {last.strftime('%d.%m.%Y %H:%M')}, {count} поз.")
            else:
                lines.append(f"• {vendor}: нет данных")
        return "\n".join(lines)

    try:
        status_text = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.error("[B24Bot] _do_status error: %s", e, exc_info=True)
        status_text = f"❌ Ошибка получения статуса: {e}"

    return [_msg(status_text, keyboard=KB_MAIN)]


# ------------------------------------------------------------------
# Background: sync single vendor
# ------------------------------------------------------------------

async def _run_sync_vendor(vendor: str, dialog_id: str) -> None:
    api = _get_api()
    loop = asyncio.get_event_loop()
    try:
        def _sync():
            service = _create_sync_service(vendor)
            return service.sync_vendor(vendor, mark_disappeared=False)

        result = await loop.run_in_executor(None, _sync)

        if result.success:
            text = (
                f"✅ [B]{vendor}[/B] синхронизирован\n"
                f"Всего: {result.total_items} поз. | "
                f"Новых: {result.new_items} | "
                f"Изменений цены: {result.price_changes_count} | "
                f"Время: {result.execution_time:.0f}с"
            )
            if result.restored_items:
                text += f" | Восстановлено: {result.restored_items}"
        else:
            text = f"❌ [B]{vendor}[/B]: ошибка — {result.error_message}"

        logger.info("[B24Bot] sync %s done: %s", vendor, result)
        await api.send_message(dialog_id, text, keyboard=KB_MAIN, replace=True)

    except Exception as e:
        logger.error("[B24Bot] _run_sync_vendor %s: %s", vendor, e, exc_info=True)
        await api.send_message(dialog_id, f"❌ Ошибка синхронизации [B]{vendor}[/B]: {e}", replace=True)


# ------------------------------------------------------------------
# Background: sync all vendors
# ------------------------------------------------------------------

async def _run_sync_all(dialog_id: str) -> None:
    api = _get_api()
    loop = asyncio.get_event_loop()
    results = []

    for vendor in AUTO_VENDORS:
        try:
            def _sync(v=vendor):
                service = _create_sync_service(v)
                return service.sync_vendor(v, mark_disappeared=False)

            result = await loop.run_in_executor(None, _sync)
            results.append(result)
            logger.info("[B24Bot] sync_all %s done: %s", vendor, result)
        except Exception as e:
            logger.error("[B24Bot] sync_all %s error: %s", vendor, e, exc_info=True)
            results.append(None)

    lines = ["[B]Синхронизация всех вендоров завершена:[/B]\n"]
    for vendor, result in zip(AUTO_VENDORS, results):
        if result is None:
            lines.append(f"❌ {vendor}: ошибка")
        elif result.success:
            lines.append(
                f"✅ {vendor}: {result.total_items} поз., "
                f"+{result.new_items} / ~{result.price_changes_count} / -{result.disappeared_items}"
            )
        else:
            lines.append(f"❌ {vendor}: {result.error_message}")

    await api.send_message(dialog_id, "\n".join(lines), keyboard=KB_MAIN, replace=True)


# ------------------------------------------------------------------
# Background: check vendor (без записи в БД)
# ------------------------------------------------------------------

async def _run_check_vendor(vendor: str, dialog_id: str) -> None:
    api = _get_api()
    loop = asyncio.get_event_loop()
    try:
        def _check():
            service = _create_sync_service(vendor)
            return service.check_price_changes(vendor)

        result = await loop.run_in_executor(None, _check)
        text = _format_check_result(vendor, result)
        logger.info("[B24Bot] check %s done: changes=%s", vendor, result.has_changes)
        await api.send_message(dialog_id, text, keyboard=KB_MAIN, replace=True)

    except Exception as e:
        logger.error("[B24Bot] _run_check_vendor %s: %s", vendor, e, exc_info=True)
        await api.send_message(dialog_id, f"❌ Ошибка проверки [B]{vendor}[/B]: {e}", replace=True)


def _format_check_result(vendor: str, result) -> str:
    if not result.has_changes:
        return f"✅ [B]{vendor}[/B]: изменений нет (в файле {result.total_in_file} поз., в БД {result.total_in_db} поз.)"

    lines = [f"[B]{vendor}[/B]: обнаружены изменения\n"]
    lines.append(f"В файле: {result.total_in_file} | В БД: {result.total_in_db}")

    if result.new_items_count:
        lines.append(f"➕ Новых позиций: {result.new_items_count}")

    if result.updated_items_count:
        lines.append(f"💱 Изменений цены: {result.updated_items_count}")
        if result.avg_price_change_percent:
            lines.append(f"   Среднее изменение: {result.avg_price_change_percent:+.1f}%")
        if result.max_price_increase:
            pc = result.max_price_increase
            lines.append(f"   Макс. рост: {pc.article} {pc.price_diff_percent:+.1f}%")
        if result.max_price_decrease:
            pc = result.max_price_decrease
            lines.append(f"   Макс. снижение: {pc.article} {pc.price_diff_percent:+.1f}%")

    if result.disappeared_items_count:
        lines.append(f"➖ Исчезло позиций: {result.disappeared_items_count}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Shina (cable bus prices)
# ------------------------------------------------------------------

async def _do_shina() -> list[dict]:
    loop = asyncio.get_event_loop()

    def _fetch() -> tuple:
        service = ShinaService(SqlRepository(settings.DATABASE_URL), parser=None)
        return service.get_current_prices(), service.get_config_count()

    try:
        prices, count = await loop.run_in_executor(None, _fetch)
        copper = prices.get('медь', '—')
        alum = prices.get('алюм', '—')
        text = (
            f"[B]Цены шин (ШИНА):[/B]\n\n"
            f"🔴 Медь: {copper} руб/кг\n"
            f"⚪ Алюминий: {alum} руб/кг\n\n"
            f"📦 Позиций в конфиге: {count}\n\n"
            f"Обновить: [B]шина медь 1400[/B]  или  [B]шина алюм 500[/B]"
        )
    except Exception as e:
        logger.error("[B24Bot] _do_shina error: %s", e, exc_info=True)
        text = f"❌ Ошибка получения цен шин: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


async def _do_shina_update(args: str) -> list[dict]:
    parts = args.split()
    if len(parts) < 2:
        return [_msg("Формат: [B]шина медь 1400[/B]  или  [B]шина алюм 500[/B]", keyboard=KB_MAIN)]

    material_input = parts[0]
    try:
        price = float(parts[1].replace(',', '.'))
    except ValueError:
        return [_msg(f"❌ Неверная цена: {parts[1]}", keyboard=KB_MAIN)]

    if MATERIAL_ALIASES.get(material_input.lower()) is None:
        return [_msg(f"❌ Неизвестный материал: [B]{material_input}[/B]. Допустимые: медь, алюм", keyboard=KB_MAIN)]

    loop = asyncio.get_event_loop()

    def _update() -> int:
        service = ShinaService(SqlRepository(settings.DATABASE_URL), parser=None)
        return service.update_price(material_input, price)

    try:
        updated = await loop.run_in_executor(None, _update)
        if updated == 0:
            text = f"⚠️ Материал [B]{material_input}[/B] не найден в конфиге шин (загрузите Excel-файл сначала)"
        else:
            material_norm = MATERIAL_ALIASES[material_input.lower()]
            label = "Медь" if material_norm == 'медь' else "Алюминий"
            text = f"✅ [B]{label}[/B]: {price:.0f} руб/кг, пересчитано {updated} поз."
    except Exception as e:
        logger.error("[B24Bot] _do_shina_update error: %s", e, exc_info=True)
        text = f"❌ Ошибка обновления цены шин: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


# ------------------------------------------------------------------
# Discontinued products
# ------------------------------------------------------------------

async def _do_disc_list(vendor: str) -> list[dict]:
    loop = asyncio.get_event_loop()

    def _fetch() -> list:
        repo = SqlRepository(settings.DATABASE_URL)
        return repo.get_discontinued_items(vendor)

    try:
        items = await loop.run_in_executor(None, _fetch)
        if not items:
            text = f"✅ [B]{vendor}[/B]: снятых с производства нет"
        else:
            lines = [f"[B]{vendor}[/B]: снято с производства — {len(items)} поз.\n"]
            for item in items[:50]:
                part = item['part_num']
                replacement = item.get('storage', '')
                lines.append(f"• {part}" + (f" → {replacement}" if replacement else ""))
            if len(items) > 50:
                lines.append(f"... и ещё {len(items) - 50} поз.")
            text = "\n".join(lines)
    except Exception as e:
        logger.error("[B24Bot] _do_disc_list %s: %s", vendor, e, exc_info=True)
        text = f"❌ Ошибка получения списка: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


async def _do_disc_mark(args: str) -> list[dict]:
    parts = args.split()
    if len(parts) < 2:
        return [_msg("Формат: [B]пометить VENDOR АРТИКУЛ[/B]  или  [B]пометить VENDOR АРТИКУЛ ЗАМЕНА[/B]", keyboard=KB_MAIN)]

    vendor = parts[0].upper()
    article = parts[1]
    replacement = parts[2] if len(parts) > 2 else None

    if vendor not in AUTO_VENDORS:
        return [_msg(f"❌ Неизвестный вендор: [B]{vendor}[/B]. Доступны: {', '.join(AUTO_VENDORS)}", keyboard=KB_MAIN)]

    loop = asyncio.get_event_loop()

    def _mark() -> bool:
        repo = SqlRepository(settings.DATABASE_URL)
        return repo.mark_as_discontinued(vendor, article, replacement)

    try:
        found = await loop.run_in_executor(None, _mark)
        if found:
            repl_text = f" (замена: {replacement})" if replacement else ""
            text = f"✅ [B]{vendor} {article}[/B] помечен как снятый с производства{repl_text}"
        else:
            text = f"❌ Позиция [B]{vendor} {article}[/B] не найдена в базе"
    except Exception as e:
        logger.error("[B24Bot] _do_disc_mark error: %s", e, exc_info=True)
        text = f"❌ Ошибка пометки: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


async def _do_disc_unmark(args: str) -> list[dict]:
    parts = args.split()
    if len(parts) < 2:
        return [_msg("Формат: [B]восстановить VENDOR АРТИКУЛ[/B]", keyboard=KB_MAIN)]

    vendor = parts[0].upper()
    article = parts[1]

    if vendor not in AUTO_VENDORS:
        return [_msg(f"❌ Неизвестный вендор: [B]{vendor}[/B]. Доступны: {', '.join(AUTO_VENDORS)}", keyboard=KB_MAIN)]

    loop = asyncio.get_event_loop()

    def _unmark() -> bool:
        repo = SqlRepository(settings.DATABASE_URL)
        return repo.unmark_discontinued(vendor, article)

    try:
        found = await loop.run_in_executor(None, _unmark)
        if found:
            text = f"✅ [B]{vendor} {article}[/B] — пометка снята, статус активен"
        else:
            text = f"❌ Позиция [B]{vendor} {article}[/B] не найдена среди снятых"
    except Exception as e:
        logger.error("[B24Bot] _do_disc_unmark error: %s", e, exc_info=True)
        text = f"❌ Ошибка снятия пометки: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


async def _do_disc_batch(lines: list) -> list[dict]:
    """Массовая пометка снятых с производства из многострочного сообщения.

    Каждая строка: VENDOR АРТИКУЛ [ЗАМЕНА]
    """
    items = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        vendor = parts[0].upper()
        article = parts[1]
        replacement = parts[2] if len(parts) > 2 else None
        if vendor in AUTO_VENDORS:
            items.append((vendor, article, replacement))

    if not items:
        return [_msg(
            "⚠️ Нет распознанных строк.\n\n"
            "Формат:\n[B]пометить-список[/B]\nKEAZ 103505 333147\nKEAZ 103507\nEKF mdse-47-pro RDE4716",
            keyboard=KB_MAIN,
        )]

    loop = asyncio.get_event_loop()

    def _mark_batch():
        repo = SqlRepository(settings.DATABASE_URL)
        marked = 0
        not_found = []
        for vendor, article, replacement in items:
            ok = repo.mark_as_discontinued(vendor, article, replacement)
            if ok:
                marked += 1
            else:
                not_found.append(f"{vendor} {article}")
        return marked, not_found

    try:
        marked, not_found = await loop.run_in_executor(None, _mark_batch)
        out = [f"✅ Помечено: [B]{marked}[/B] из {len(items)} позиций."]
        if not_found:
            out.append(f"⚠️ Не найдено в БД: {len(not_found)}")
            for ex in not_found[:10]:
                out.append(f"  • {ex}")
        text = "\n".join(out)
    except Exception as e:
        logger.error("[B24Bot] _do_disc_batch error: %s", e, exc_info=True)
        text = f"❌ Ошибка импорта: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


# ------------------------------------------------------------------
# Help text
# ------------------------------------------------------------------

def _help_text() -> str:
    return (
        "[B]Price Sync Bot[/B]\n\n"
        "📊 [B]статус[/B] — последняя синхронизация по каждому вендору\n"
        "🔄 [B]синхронизировать[/B] — синхронизировать выбранного вендора\n"
        "🔍 [B]проверить[/B] — проверить изменения без записи в БД\n"
        "🔄 [B]sync_all[/B] — синхронизировать всех вендоров\n"
        "⚡ [B]шина[/B] — цены на медь/алюминий; [B]шина медь 1400[/B] — обновить\n"
        "🚫 [B]снятые[/B] — снятые с производства; [B]пометить VENDOR АРТ[/B] / [B]восстановить VENDOR АРТ[/B]\n"
        "   Список: [B]пометить-список[/B] (затем VENDOR АРТ ЗАМЕНА на каждой строке)"
    )
