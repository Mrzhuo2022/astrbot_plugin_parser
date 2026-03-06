import asyncio
import shutil
import time
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger

from .config import PluginConfig


class CacheCleaner:
    """
    每天固定时间自动清理插件缓存目录的调度器封装。
    """

    JOBNAME = "CacheCleaner"
    _RECENT_FILE_GRACE_SEC = 120

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.scheduler = AsyncIOScheduler(timezone=self.cfg.timezone)
        self.scheduler.start()

        self.register_task()

        logger.info(
            f"{self.JOBNAME} 已启动，任务周期：{self.cfg.clean_cron}, "
            f"缓存阈值：{self.cfg.cache_max_size_gb} GB"
        )

    def register_task(self):
        cron_expr = str(self.cfg.clean_cron or "").strip()
        if not cron_expr:
            logger.info(f"[{self.JOBNAME}] clean_cron 为空，已禁用自动清理")
            return

        try:
            self.trigger = CronTrigger.from_crontab(cron_expr)
            self.scheduler.add_job(
                func=self._clean_plugin_cache,
                trigger=self.trigger,
                name=f"{self.JOBNAME}_scheduler",
                max_instances=1,
            )
        except Exception as e:
            logger.error(f"[{self.JOBNAME}] Cron 格式错误：{e}")

    @staticmethod
    def _calc_dir_size(dir_path: Path) -> int:
        total = 0
        if not dir_path.exists():
            return total
        for path in dir_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _trim_to_size(
        dir_path: Path,
        target_size: int,
        recent_grace_sec: int,
    ) -> tuple[int, int, int]:
        """
        删除最旧文件，直到目录体积不超过阈值。

        Returns:
            tuple[removed_count, freed_bytes, remain_bytes]
        """
        files: list[tuple[Path, float, int]] = []
        total = 0
        for path in dir_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            files.append((path, st.st_mtime, st.st_size))
            total += st.st_size

        if total <= target_size:
            return 0, 0, total

        files.sort(key=lambda x: x[1])  # 最旧优先
        now = time.time()
        removed_count = 0
        freed_bytes = 0

        for path, mtime, size in files:
            if total <= target_size:
                break
            # 跳过近期文件，降低与发送/下载并发冲突概率
            if now - mtime < recent_grace_sec:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= size
            freed_bytes += size
            removed_count += 1

        return removed_count, freed_bytes, max(total, 0)

    async def _clean_plugin_cache(self) -> None:
        """按策略清理缓存目录"""
        loop = asyncio.get_running_loop()
        try:
            if self.cfg.cache_max_size > 0:
                size = await loop.run_in_executor(
                    None, self._calc_dir_size, self.cfg.cache_dir
                )
                if size <= self.cfg.cache_max_size:
                    logger.info(
                        f"[{self.JOBNAME}] 当前缓存 "
                        f"{size / 1024 / 1024 / 1024:.2f} GB，未达到阈值 "
                        f"{self.cfg.cache_max_size_gb} GB，跳过清理"
                    )
                    return
                logger.warning(
                    f"[{self.JOBNAME}] 当前缓存 "
                    f"{size / 1024 / 1024 / 1024:.2f} GB，超过阈值 "
                    f"{self.cfg.cache_max_size_gb} GB，开始清理"
                )
                removed_count, freed_bytes, remain_bytes = await loop.run_in_executor(
                    None,
                    self._trim_to_size,
                    self.cfg.cache_dir,
                    self.cfg.cache_max_size,
                    self._RECENT_FILE_GRACE_SEC,
                )
                if removed_count == 0:
                    logger.warning(
                        f"[{self.JOBNAME}] 未删除任何文件，当前缓存 "
                        f"{remain_bytes / 1024 / 1024 / 1024:.2f} GB"
                    )
                    return
                logger.info(
                    f"[{self.JOBNAME}] 已删除 {removed_count} 个文件，释放 "
                    f"{freed_bytes / 1024 / 1024 / 1024:.2f} GB，当前缓存 "
                    f"{remain_bytes / 1024 / 1024 / 1024:.2f} GB"
                )
                return

            await loop.run_in_executor(None, shutil.rmtree, self.cfg.cache_dir)
            self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Cache directory cleaned and recreated.")
        except Exception:
            logger.exception("Error while cleaning cache directory.")

    async def stop(self):
        try:
            self.scheduler.remove_all_jobs()
            self.scheduler.shutdown(wait=False)
            logger.info(f"[{self.JOBNAME}] 已停止")
        except Exception:
            logger.exception(f"[{self.JOBNAME}] 停止调度器失败")
