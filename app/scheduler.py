import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Config
from app.garmin_client import GarminClient
from app.storage import Storage

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

logger = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage
        self.scheduler = BlockingScheduler()
        self._setup_job()

    def _setup_job(self):
        """设置定时同步任务"""
        interval = self.config.sync_interval_hours
        cron_expr = self.config.cron_expression

        if cron_expr:
            # Parse cron expression
            parts = cron_expr.split()
            if len(parts) >= 5:
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                )
                logger.info(f"设置定时同步任务: cron={cron_expr}")
            else:
                raise ValueError(f"Invalid cron expression: {cron_expr}")
        else:
            from apscheduler.triggers.interval import IntervalTrigger

            trigger = IntervalTrigger(hours=interval)
            logger.info(f"设置定时同步任务: interval={interval}小时")

        self.scheduler.add_job(self._sync_task, trigger, id="garmin_sync")

    def _sync_task(self, fetch_details: bool = True):
        """执行同步任务"""
        logger.info("开始执行同步任务")
        client = GarminClient(self.config)
        try:
            client.login()
            activities = client.sync_recent_activities(days=7)
            fetched, new = self.storage.save_activities(activities)
            logger.info(f"同步完成: 获取 {fetched} 条, 新增 {new} 条")

            # Phase 2: 获取详情
            details_fetched = 0
            details_failed = 0

            if fetch_details:
                activity_ids = self.storage.get_activities_without_details(limit=100)
                if activity_ids:
                    logger.info(f"正在获取 {len(activity_ids)} 个活动的详情...")
                    for activity_id in activity_ids:
                        try:
                            details = client.get_activity_details(activity_id)
                            splits = client.get_activity_splits(activity_id)
                            self.storage.save_activity_details(activity_id, details, splits)
                            details_fetched += 1
                            # 限流由 client.rate_limiter 统一控制
                        except Exception as e:
                            logger.warning(f"获取详情失败 (ID: {activity_id}): {e}")
                            details_failed += 1

                    logger.info(f"详情获取完成: 成功 {details_fetched}, 失败 {details_failed}")

            self.storage.log_sync(fetched, new, "success", details_fetched=details_fetched, details_failed=details_failed)
        except Exception as e:
            logger.error(f"同步失败: {e}")
            self.storage.log_sync(0, 0, f"error: {e}")
            raise
        finally:
            client.logout()

    def start(self):
        """启动调度器"""
        logger.info("启动定时调度器")
        self.scheduler.start()

    def stop(self):
        """停止调度器"""
        logger.info("停止定时调度器")
        self.scheduler.shutdown(wait=False)

    def run_now(self):
        """立即执行一次同步"""
        self._sync_task()
