"""Планировщик задач: ERP-синхронизация, обновление прайсов, backfill VFF."""

import asyncio
import logging
import os
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application

from config.settings import settings

_TZ = ZoneInfo(os.getenv('TZ', 'Europe/Moscow'))

logger = logging.getLogger(__name__)


# ─── Надёжная доставка уведомлений ───────────────────────────────────────────
# Telegram-запросы идут через httpx (внутри python-telegram-bot) и иногда
# рвутся на прокси/сети: httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError.
# Это НЕ ошибка бизнес-логики — это обрыв транспорта. Поэтому уведомления
# отправляем с ретраями и backoff, а ошибку доставки отделяем от ошибки
# самой синхронизации (иначе в Telegram прилетает вводящее в заблуждение
# «Ошибка авто-ERP», хотя данные в БД уже обновились).

_NOTIFY_MAX_ATTEMPTS = 3
_NOTIFY_BACKOFF_BASE = 2.0  # секунды: 2, 4, 8
# Жёсткий дедлайн на ОДНУ попытку отправки.
# Нужен потому, что httpx read_timeout НЕ применяется к SOCKS5-handshake
# (httpcore/_async/socks_proxy.py: _init_socks5_connection) — при недоступном
# прокси соединение висит до TCP-таймаута ОС (~15-17 мин на Linux) и блокирует
# event loop, т.е. весь бот. wait_for ставит свою границу поверх этого.
_NOTIFY_HARD_TIMEOUT = 45.0


def _is_transport_error(exc: Exception) -> bool:
    """True, если исключение — обрыв транспорта (повторять имеет смысл).

    Иерархия httpx: TransportError → {TimeoutException, NetworkError, ProtocolError}
    TimeoutException и NetworkError — РАЗНЫЕ ветки, поэтому берём корневой
    TransportError как catch-all, а конкретные классы — для читаемости.

    Классы собираем через getattr + фильтр None: набор имён в httpx менялся
    между версиями, и обращение к отсутствующему атрибуту не должно ронять
    проверку (иначе потеряется и ретрай, и диагностика).
    """
    # asyncio.TimeoutError — наш жёсткий дедлайн (Python 3.11+: это же TimeoutError)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    # httpx/requests импортируем лениво — чтобы модуль грузился и без них
    transport_types: list = []
    try:
        import httpx
        for name in (
            'TransportError',   # catch-all: Read/Connect/Timeout/Protocol
            'ReadError',
            'ConnectError',
            'ConnectTimeout',
            'ReadTimeout',
            'WriteTimeout',
            'PoolTimeout',
            'RemoteProtocolError',
            'NetworkError',
            'TimeoutException',
        ):
            cls = getattr(httpx, name, None)
            if isinstance(cls, type) and issubclass(cls, BaseException):
                transport_types.append(cls)
    except ImportError:
        pass
    try:
        import requests
        for name in ('ConnectionError', 'Timeout'):
            cls = getattr(requests.exceptions, name, None)
            if isinstance(cls, type) and issubclass(cls, BaseException):
                transport_types.append(cls)
    except (ImportError, AttributeError):
        pass
    return bool(transport_types) and isinstance(exc, tuple(transport_types))


def _is_erp_transport_error(exc: Exception) -> bool:
    """True, если ошибка — обрыв связи именно с 1С-ERP (requests-уровень)."""
    try:
        import requests
    except ImportError:
        return False
    return isinstance(exc, (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    ))


async def _send_telegram_safe(
    bot,
    chat_id,
    text: str,
    attempts: int = _NOTIFY_MAX_ATTEMPTS,
    hard_timeout: float = _NOTIFY_HARD_TIMEOUT,
) -> bool:
    """Отправляет сообщение в Telegram с ретраями на транспортных ошибках.

    Каждая попытка ограничена `hard_timeout` через asyncio.wait_for — это
    спасает от зависания на SOCKS5-handshake, где httpx read_timeout не работает
    и соединение висит до TCP-таймаута ОС (наблюдалось ~17 минут).

    Возвращает True, если сообщение доставлено, False — если все попытки исчерпаны.
    Транспортные ошибки (ReadError/ConnectError/Timeout) повторяем с backoff;
    остальные (например, TelegramError 400) — нет, они не пройдут.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await asyncio.wait_for(
                bot.send_message(chat_id=chat_id, text=text),
                timeout=hard_timeout,
            )
            if attempt > 1:
                logger.info("[SCHEDULER] Уведомление доставлено с %d-й попытки", attempt)
            return True
        except asyncio.TimeoutError:
            last_exc = asyncio.TimeoutError(
                f"превышен дедлайн {hard_timeout:.0f}с на отправку (вероятно, завис SOCKS5-handshake)"
            )
            logger.warning(
                "[SCHEDULER] Таймаут доставки уведомления (%.0fс), попытка %d/%d",
                hard_timeout, attempt, attempts,
            )
            if attempt == attempts:
                break
            await asyncio.sleep(_NOTIFY_BACKOFF_BASE * (2 ** (attempt - 1)))
            continue
        except Exception as e:
            last_exc = e
            if not _is_transport_error(e) or attempt == attempts:
                break
            delay = _NOTIFY_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "[SCHEDULER] Ошибка доставки уведомления (%s), попытка %d/%d, повтор через %.0fс",
                type(e).__name__, attempt, attempts, delay,
            )
            await asyncio.sleep(delay)

    logger.error("[SCHEDULER] Уведомление не доставлено после %d попыток: %s",
                 attempts, last_exc, exc_info=True)
    return False


def register_jobs(app: Application) -> None:
    """Регистрирует все периодические задачи в JobQueue бота."""
    jq = app.job_queue
    if jq is None:
        logger.warning("JobQueue недоступен — задачи не запланированы")
        return

    chat_id = settings.NOTIFY_CHAT_ID
    if not chat_id:
        logger.warning("NOTIFY_CHAT_ID не задан — автоматические задачи не будут отправлять уведомления")
        return

    # --- ERP: несколько раз в день ---
    erp_times = settings.ERP_SYNC_TIMES  # список datetime.time
    if settings.ERP_BASE_URL and erp_times:
        for t in erp_times:
            t_aware = t.replace(tzinfo=_TZ)
            jq.run_daily(
                _job_erp_sync,
                time=t_aware,
                chat_id=chat_id,
                name=f"erp_sync_{t.strftime('%H%M')}",
            )
        times_str = ", ".join(t.strftime("%H:%M") for t in erp_times)
        logger.info(f"[SCHEDULER] ERP-синхронизация запланирована на: {times_str}")
    else:
        logger.info("[SCHEDULER] ERP-синхронизация не запланирована (нет ERP_BASE_URL или ERP_SYNC_TIMES)")

    # --- Синхронизация прайсов ---
    sync_days = settings.SYNC_ALL_DAYS     # tuple[int, ...], 0=пн (Python-стиль)
    sync_time = settings.SYNC_ALL_TIME     # datetime.time
    if sync_time is not None and sync_days:
        sync_time_aware = sync_time.replace(tzinfo=_TZ)
        # PTB 20+: days 0-6 = Sun-Sat. settings использует 0=Пн (Python-стиль).
        # Конвертируем: ptb_day = (python_weekday + 1) % 7
        ptb_days = tuple((d + 1) % 7 for d in sync_days)
        jq.run_daily(
            _job_sync_all,
            time=sync_time_aware,
            days=ptb_days,
            chat_id=chat_id,
            name="sync_all_weekly",
        )
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        days_str = ",".join(day_names[d] for d in sync_days)
        logger.info(f"[SCHEDULER] sync_all запланирован на {days_str} {sync_time.strftime('%H:%M')}")


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------

async def _job_erp_sync(context) -> None:
    """Периодическая синхронизация ERP с уведомлением в Telegram.

    Разделены две независимые фазы:
      1. Сама синхронизация (запись в БД) — тут ловим ошибки бизнес-логики/1С.
      2. Доставка отчёта в Telegram — тут ловим обрывы транспорта (httpx.*).
    Раньше всё было в одном try, из-за чего обрыв сети до Telegram
    выглядел как «Ошибка авто-ERP», хотя данные уже записались в БД.
    """
    chat_id = context.job.chat_id
    logger.info("[SCHEDULER] Запуск ERP-синхронизации...")

    # ── Фаза 1: сама синхронизация ─────────────────────────────────────────
    try:
        from adapters.erp.erp_client import ErpClient
        from adapters.database.sql_repository import SqlRepository
        from domain.services.erp_sync_service import ErpSyncService

        erp_client = ErpClient(
            base_url=settings.ERP_BASE_URL,
            login=settings.ONE_C_LOGIN,
            password=settings.ONE_C_PASSWORD,
        )
        repository = SqlRepository(settings.DATABASE_URL)
        service = ErpSyncService(erp_client, repository)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, service.sync_from_erp)

        logger.info("[FIX] ERP-синхронизация завершена: added=%s, updated=%s, skipped=%s",
                    result.added, result.updated, result.skipped_existing)

        has_changes = result.added > 0 or result.updated > 0
        logger.info("[FIX] Есть изменения: %s", has_changes)

        if not has_changes:
            logger.info("[SCHEDULER] Нет изменений в ERP — отчёт не отправлен")
            return

        lines = [
            "📊 Автоматическая ERP-синхронизация:",
            f"📥 Получено: {result.total_received}",
            f"➕ Добавлено: {result.added}",
            f"🔗 Привязано: {result.updated}",
            f"⏭ Пропущено: {result.skipped_existing}",
        ]
        if result.errors:
            lines.append(f"❌ Ошибок: {result.errors}")

        # Нормализуем все варианты 'по запросу' → 'Цена по запросу' по всей таблице
        if hasattr(repository, 'normalize_price_on_request_text'):
            normalized = repository.normalize_price_on_request_text()
            if normalized:
                lines.append(f"🔤 Нормализовано 'Цена по запросу': {normalized}")

        report = "\n".join(lines)

    except Exception as e:
        # Фаза 1 упала — тут честно говорим, что именно сломалось.
        logger.error("[SCHEDULER] Ошибка ERP-синхронизации: %s", e, exc_info=True)
        err_type = type(e).__name__
        detail = str(e)[:250] or 'без описания'
        if _is_erp_transport_error(e):
            detail = (
                f"Нет связи с 1С-ERP ({settings.ERP_BASE_URL}). "
                f"Проверьте ERP_BASE_URL и доступность сервиса."
            )
        await _send_telegram_safe(
            context.bot, chat_id,
            f"❌ <b>Ошибка ERP-синхронизации</b>\n\n"
            f"<b>{err_type}</b>\n{detail}",
        )
        return

    # ── Фаза 2: доставка отчёта ────────────────────────────────────────────
    delivered = await _send_telegram_safe(context.bot, chat_id, report)
    if delivered:
        logger.info("[SCHEDULER] Отчёт отправлен: added=%s, linked=%s", result.added, result.updated)
    else:
        # Отчёт не доехал, но данные в БД обновились — сообщаем об этом отдельно
        # и обязательно логируем содержимое, чтобы ничего не потерялось.
        logger.error("[SCHEDULER] Отчёт НЕ доставлен. Содержимое:\n%s", report)
        await _send_telegram_safe(
            context.bot, chat_id,
            "⚠️ <b>ERP-синхронизация выполнена, но отчёт не доставлен</b>\n\n"
            "Обрыв связи с Telegram API (httpx). Данные в БД обновлены.\n"
            f"Добавлено: {result.added}, привязано: {result.updated}.\n"
            "Подробности — в logs/sync.log",
        )


async def _job_sync_all(context) -> None:
    """Еженедельная синхронизация всех прайсов с backfill VFF."""
    chat_id = context.job.chat_id
    logger.info("[SCHEDULER] Запуск еженедельной синхронизации прайсов...")

    await _send_telegram_safe(
        context.bot, chat_id,
        "🔄 Запуск еженедельной синхронизации прайсов...",
    )

    vendors = ['KEAZ', 'ОВЕН', 'EKF', 'IEK', 'DKC', 'CHINT']
    results_lines = []

    for vendor in vendors:
        try:
            from utils.normalizer import ArticleNormalizer
            from vendors.registry import VendorRegistry
            from adapters.database.sql_repository import SqlRepository
            from domain.services.sync_service import SyncService
            from domain.services.report_service import ReportService

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
                report_service=report_service,
            )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, service.sync_vendor, vendor)

            if result.success:
                parts = [f"✅ {vendor}: {result.total_items}"]
                if result.new_items:
                    parts.append(f"➕{result.new_items}")
                if result.updated_items:
                    parts.append(f"🔄{result.updated_items}")
                if result.restored_items:
                    parts.append(f"♻️{result.restored_items}")
                if result.disappeared_items:
                    parts.append(f"👻{result.disappeared_items}")
                results_lines.append(" | ".join(parts))
            else:
                results_lines.append(f"❌ {vendor}: {result.error_message[:80]}")

        except Exception as e:
            logger.error("[SCHEDULER] Ошибка синхронизации %s: %s", vendor, e, exc_info=True)
            results_lines.append(f"❌ {vendor}: {str(e)[:80]}")

    # Backfill VendorForFilter после обновления прайсов
    backfill_count = 0
    try:
        repository = SqlRepository(settings.DATABASE_URL)
        synonyms_map = repository.get_synonyms_map()
        backfill_count = repository.backfill_vendor_for_filter(synonyms_map)
        if backfill_count:
            results_lines.append(f"\n🏷 VendorForFilter заполнен для {backfill_count} записей")
    except Exception as e:
        logger.error("[SCHEDULER] Ошибка backfill VFF: %s", e)

    report = "📊 Еженедельная синхронизация:\n\n" + "\n".join(results_lines)
    if len(report) > 4000:
        report = report[:3900] + "\n\n... (обрезано)"

    delivered = await _send_telegram_safe(context.bot, chat_id, report)
    if delivered:
        logger.info("[SCHEDULER] Еженедельная синхронизация завершена")
    else:
        logger.error("[SCHEDULER] Еженедельный отчёт НЕ доставлен. Содержимое:\n%s", report)
