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

    # --- Синхронизация прайсов: раз в неделю ---
    sync_day = settings.SYNC_ALL_DAY       # 0=пн, 6=вс
    sync_time = settings.SYNC_ALL_TIME     # datetime.time
    if sync_time is not None:
        sync_time_aware = sync_time.replace(tzinfo=_TZ)
        # PTB 20+: days 0-6 = Sun-Sat. settings.SYNC_ALL_DAY использует 0=Пн (Python-стиль).
        # Конвертируем: ptb_day = (python_weekday + 1) % 7
        ptb_day = (sync_day + 1) % 7
        jq.run_daily(
            _job_sync_all,
            time=sync_time_aware,
            days=(ptb_day,),
            chat_id=chat_id,
            name="sync_all_weekly",
        )
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        logger.info(f"[SCHEDULER] sync_all запланирован на {day_names[sync_day]} {sync_time.strftime('%H:%M')}")


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------

async def _job_erp_sync(context) -> None:
    """Периодическая синхронизация ERP с уведомлением в Telegram."""
    chat_id = context.job.chat_id
    logger.info("[SCHEDULER] Запуск ERP-синхронизации...")

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

        lines = [
            "📊 Автоматическая ERP-синхронизация:",
            f"📥 Получено: {result.total_received}",
            f"➕ Добавлено: {result.added}",
            f"🔗 Привязано: {result.updated}",
            f"⏭ Пропущено: {result.skipped_existing}",
        ]
        if result.errors:
            lines.append(f"❌ Ошибок: {result.errors}")

        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
        logger.info("[SCHEDULER] ERP-синхронизация завершена: added=%s, linked=%s", result.added, result.updated)

    except Exception as e:
        logger.error("[SCHEDULER] Ошибка ERP: %s", e, exc_info=True)
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка авто-ERP: {str(e)[:300]}")
        except Exception:
            pass


async def _job_sync_all(context) -> None:
    """Еженедельная синхронизация всех прайсов с backfill VFF."""
    chat_id = context.job.chat_id
    logger.info("[SCHEDULER] Запуск еженедельной синхронизации прайсов...")

    await context.bot.send_message(chat_id=chat_id, text="🔄 Запуск еженедельной синхронизации прайсов...")

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

    await context.bot.send_message(chat_id=chat_id, text=report)
    logger.info("[SCHEDULER] Еженедельная синхронизация завершена")
