import random
import threading
import time
from typing import Optional


class RateLimiter:
    """API 请求限流器

    控制 API 调用频率，支持：
    - 固定间隔或每秒调用次数
    - 随机抖动避免雷群效应
    - 非高峰时段检测
    """

    def __init__(
        self,
        calls_per_second: float = 2.0,
        jitter: bool = True,
        off_peak_hours: Optional[list] = None,
    ):
        """初始化限流器

        Args:
            calls_per_second: 每秒调用次数，默认 2.0（即 0.5 秒间隔）
            jitter: 是否添加随机抖动（50%~150%）
            off_peak_hours: 非高峰时段小时列表，如 [2, 3, 4, 5] 表示凌晨 2-5 点
        """
        self.interval = 1.0 / calls_per_second
        self.jitter = jitter
        self.off_peak_hours = off_peak_hours or []
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self):
        """等待直到可以发送下一个请求"""
        with self.lock:
            now = time.time()
            wait_time = self.interval
            if self.jitter:
                wait_time *= (0.5 + random.random())  # 50%~150% 抖动
            elapsed = now - self.last_call
            if elapsed < wait_time:
                time.sleep(wait_time - elapsed)
            self.last_call = time.time()

    def is_off_peak(self) -> bool:
        """检查是否在非高峰时段"""
        current_hour = time.localtime().tm_hour
        return current_hour in self.off_peak_hours

    def get_actual_interval(self) -> float:
        """获取实际间隔时间（考虑抖动）"""
        if self.jitter:
            return self.interval * (0.5 + random.random())
        return self.interval
