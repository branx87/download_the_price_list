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
from typing import Optional

from config.settings import settings
from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository
from domain.services.sync_service import SyncService
from domain.services.report_service import ReportService
from adapters.bitrix.bitrix_api import BitrixBotAPI

logger = logging.getLogger(__name__)

# Вендоры с автоматической загрузкой файла (без ручной загрузки)
AUTO_VENDORS = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']


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
]

KB_SYNC = _vendor_kb("sync")
KB_CHECK = _vendor_kb("check")


# ------------------------------------------------------------------
# Message builder
# ------------------------------------------------------------------

def _msg(text: str, keyboard: Optional[list] = None) -> dict:
    m: dict = {"text": text}
    if keyboard is not None:
        m["keyboard"] = keyboard
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
    return BitrixBotAPI(settings.BITRIX_REST_URL, settings.BITRIX_BOT_ID)


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
    if not _is_admin(from_user_id):
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
        return [_msg("⏳ Запускаю синхронизацию всех вендоров...\nРезультат придёт сюда после завершения.")]

    if raw.startswith("sync_"):
        vendor = raw[5:].upper()
        if vendor in AUTO_VENDORS:
            asyncio.create_task(_run_sync_vendor(vendor, dialog_id))
            return [_msg(f"⏳ Запускаю синхронизацию [B]{vendor}[/B]...\nРезультат придёт сюда после завершения.")]

    if raw.startswith("check_"):
        vendor = raw[6:].upper()
        if vendor in AUTO_VENDORS:
            asyncio.create_task(_run_check_vendor(vendor, dialog_id))
            return [_msg(f"⏳ Запускаю проверку [B]{vendor}[/B]...\nРезультат придёт сюда после завершения.")]

    return [_msg(_help_text(), keyboard=KB_MAIN)]


# ------------------------------------------------------------------
# Status — читаем из БД
# ------------------------------------------------------------------

async def _do_status() -> list[dict]:
    loop = asyncio.get_event_loop()

    def _fetch() -> str:
        repo = SqlRepository(settings.DATABASE_URL)
        lines = ["[B]Статус синхронизации:[/B]\n"]
        for vendor in AUTO_VENDORS:
            last = repo.get_vendor_last_update(vendor)
            count = repo.get_vendor_total_count(vendor)
            if last:
                lines.append(f"• {vendor}: {last.strftime('%d.%m.%Y %H:%M')}, {count} поз.")
            else:
                lines.append(f"• {vendor}: нет данных")
        return "\n".join(lines)

    try:
        text = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.error("[B24Bot] _do_status error: %s", e, exc_info=True)
        text = f"❌ Ошибка получения статуса: {e}"

    return [_msg(text, keyboard=KB_MAIN)]


# ------------------------------------------------------------------
# Background: sync single vendor
# ------------------------------------------------------------------

async def _run_sync_vendor(vendor: str, dialog_id: str) -> None:
    api = _get_api()
    loop = asyncio.get_event_loop()
    try:
        def _sync():
            service = _create_sync_service(vendor)
            return service.sync_vendor(vendor)

        result = await loop.run_in_executor(None, _sync)

        if result.success:
            text = (
                f"✅ [B]{vendor}[/B] синхронизирован\n"
                f"Всего: {result.total_items} поз. | "
                f"Новых: {result.new_items} | "
                f"Изменений цены: {result.price_changes_count} | "
                f"Исчезло: {result.disappeared_items} | "
                f"Время: {result.execution_time:.0f}с"
            )
        else:
            text = f"❌ [B]{vendor}[/B]: ошибка — {result.error_message}"

        logger.info("[B24Bot] sync %s done: %s", vendor, result)
        await api.send_message(dialog_id, text, keyboard=KB_MAIN)

    except Exception as e:
        logger.error("[B24Bot] _run_sync_vendor %s: %s", vendor, e, exc_info=True)
        await api.send_message(dialog_id, f"❌ Ошибка синхронизации [B]{vendor}[/B]: {e}")


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
                return service.sync_vendor(v)

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

    await api.send_message(dialog_id, "\n".join(lines), keyboard=KB_MAIN)


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
        await api.send_message(dialog_id, text, keyboard=KB_MAIN)

    except Exception as e:
        logger.error("[B24Bot] _run_check_vendor %s: %s", vendor, e, exc_info=True)
        await api.send_message(dialog_id, f"❌ Ошибка проверки [B]{vendor}[/B]: {e}")


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
# Help text
# ------------------------------------------------------------------

def _help_text() -> str:
    return (
        "[B]Price Sync Bot[/B]\n\n"
        "📊 [B]статус[/B] — последняя синхронизация по каждому вендору\n"
        "🔄 [B]синхронизировать[/B] — синхронизировать выбранного вендора\n"
        "🔍 [B]проверить[/B] — проверить изменения без записи в БД\n"
        "🔄 [B]sync_all[/B] — синхронизировать всех вендоров"
    )
