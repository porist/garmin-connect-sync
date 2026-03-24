import json
import logging
import os
import random
import socket
import time
from calendar import monthrange
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import List, Optional

# 抑制第三方库的详细日志（在导入 garminconnect 之前设置）
logging.getLogger("garth").setLevel(logging.CRITICAL)
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

import garminconnect
import requests
import garth.exc
from garminconnect import Garmin

from app.config import Config
from app.models import Activity
from app.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class GarminAPIError(Exception):
    """Garmin API 错误基类"""
    pass


class GarminAuthError(GarminAPIError):
    """认证失败（429限流、登录失败）"""
    pass


class GarminNetworkError(GarminAPIError):
    """网络问题（超时、连接失败）"""
    pass

# API 单次请求最大天数限制
MAX_DAYS_PER_REQUEST = 30


def _retry_on_rate_limit(
    max_retries: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 300.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
):
    """429 限流退避重试装饰器

    当遇到 GarminAuthError (429) 时，使用指数退避策略重试。
    超过最大重试次数后返回 None。

    Args:
        max_retries: 最大重试次数
        initial_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_factor: 退避因子
        jitter: 是否添加随机抖动
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except GarminAuthError as e:
                    if "429" not in str(e):
                        raise
                    if attempt == max_retries - 1:
                        logger.error(
                            f"达到最大重试次数 ({max_retries})，返回空结果"
                        )
                        return None
                    # 尝试从异常中获取 Retry-After 头部
                    retry_after = None
                    if hasattr(e, 'response') and e.response is not None:
                        retry_after = e.response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait_time = int(retry_after)
                        except ValueError:
                            wait_time = delay
                    else:
                        wait_time = delay
                    # 添加随机抖动
                    if jitter:
                        wait_time *= (0.5 + random.random() * 0.5)
                    logger.warning(
                        f"限流触发，等待 {wait_time:.1f}秒后重试 (第 {attempt+1}/{max_retries} 次)"
                    )
                    time.sleep(wait_time)
                    delay = min(delay * backoff_factor, max_delay)
            return None

        return wrapper

    return decorator


class GarminClient:
    def __init__(self, config: Config):
        self.config = config
        self.client: Optional[Garmin] = None
        # 创建限流器
        calls_per_second = 1.0 / config.rate_limit_request_delay_seconds
        self.rate_limiter = RateLimiter(
            calls_per_second=calls_per_second,
            jitter=config.rate_limit_jitter,
            off_peak_hours=config.rate_limit_off_peak_hours,
        )

    def login(self):
        """登录 Garmin Connect 账号，带退避重试和 Token 持久化"""
        # 非高峰时段检测
        if not self.rate_limiter.is_off_peak() and self.config.garmin_login_off_peak_only:
            wait_hours = self._get_next_off_peak_hours()
            logger.info(f"当前非高峰时段，等待 {wait_hours:.1f} 小时后重试...")
            time.sleep(wait_hours * 3600)

        # 尝试加载已有 Token
        if self._load_token():
            logger.info("已加载已有 Token，尝试使用已认证会话")
            return

        # 执行带退避重试的登录
        max_retries = self.config.garmin_login_max_retries
        delay = self.config.garmin_login_initial_retry_delay

        for attempt in range(max_retries):
            try:
                self._do_login()
                self._save_token()
                return
            except GarminAuthError as e:
                if "429" not in str(e):
                    raise
                if attempt == max_retries - 1:
                    logger.error(f"登录重试次数耗尽 ({max_retries} 次)")
                    raise
                logger.warning(f"登录限流 (429)，等待 {delay:.1f} 秒后重试 (第 {attempt+1}/{max_retries} 次)")
                time.sleep(delay)
                delay = min(delay * 2, 1800)  # 指数退避，最大 30 分钟

    def _do_login(self):
        """执行实际的登录操作"""
        self.rate_limiter.wait()
        try:
            self.client = Garmin(
                self.config.garmin_email,
                self.config.garmin_password,
                is_cn=False,  # 国际版
            )
            if self.config.garmin_mfa_code:
                self.client.login(mfa_code=self.config.garmin_mfa_code)
            else:
                self.client.login()
            # 设置请求超时
            if hasattr(self.client, 'garth'):
                self.client.garth.configure(timeout=self.config.garmin_timeout)
                logger.info(f"Garmin Connect 请求超时设置为 {self.config.garmin_timeout} 秒")
            logger.info("Garmin Connect 登录成功")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError("登录失败，请检查网络或稍后重试")
        except garth.exc.GarthHTTPError as e:
            # 检查 429 限流 - 状态码在 error.response 中
            if hasattr(e, 'error') and hasattr(e.error, 'response') and e.error.response is not None:
                if e.error.response.status_code == 429:
                    raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError("登录失败，请检查网络或稍后重试")
        except garminconnect.GarminConnectConnectionError as e:
            # garminconnect 包装了 GarthHTTPError
            if "429" in str(e):
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError("登录失败，请检查网络或稍后重试")
        except requests.exceptions.Timeout:
            raise GarminNetworkError("连接 Garmin 超时，请检查网络后重试")
        except (requests.exceptions.ConnectionError, socket.timeout):
            raise GarminNetworkError("无法连接 Garmin 服务器，请检查网络")
        except Exception as e:
            raise GarminAPIError("登录失败，请检查网络或稍后重试")

    def _get_token_path(self) -> Path:
        """获取 Token 文件路径"""
        if self.config.garmin_token_file:
            return Path(os.path.expanduser(self.config.garmin_token_file))
        return Path.home() / ".garminconnect" / "token.json"

    def _load_token(self) -> bool:
        """尝试加载已有 Token，返回是否成功"""
        token_path = self._get_token_path()
        if not token_path.exists():
            return False
        try:
            self.client = Garmin.from_existing_token(token_path)
            # 验证 Token 是否有效
            if hasattr(self.client, 'garth'):
                self.client.garth.configure(timeout=self.config.garmin_timeout)
            logger.info(f"已从 {token_path} 加载 Token")
            return True
        except Exception as e:
            logger.debug(f"加载 Token 失败: {e}")
            return False

    def _save_token(self):
        """保存 Token 到文件"""
        if not self.client or not hasattr(self.client, 'garth'):
            return
        token_path = self._get_token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.garth.dump(token_path)
            logger.info(f"Token 已保存到 {token_path}")
        except Exception as e:
            logger.warning(f"保存 Token 失败: {e}")

    def _get_next_off_peak_hours(self) -> float:
        """计算到下一个非高峰时段的等待小时数"""
        current_hour = datetime.now().hour
        off_peak = self.rate_limiter.off_peak_hours
        if not off_peak:
            return 0.0
        # 找到下一个非高峰时段
        for hour in sorted(off_peak):
            if hour > current_hour:
                return hour - current_hour
        # 明天第一个非高峰时段
        return (24 - current_hour) + sorted(off_peak)[0]

    def logout(self):
        """登出"""
        if self.client:
            try:
                self.client.logout()
                logger.info("Garmin Connect 已登出")
            except Exception as e:
                logger.warning(f"登出时出错: {e}")

    def ping(self, timeout: int = 10) -> tuple[bool, str]:
        """测试 Garmin Connect API 连通性（无需认证）"""
        try:
            resp = requests.get(
                "https://sso.garmin.com/sso/embed",
                params={"id": "gauth-widget", "embedWidget": "true"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                return True, "Garmin Connect 服务器连接正常"
            return False, f"服务器返回异常状态码: {resp.status_code}"
        except requests.exceptions.Timeout:
            return False, "连接超时，请检查网络"
        except requests.exceptions.ConnectionError:
            return False, "无法连接 Garmin 服务器，请检查网络"
        except Exception as e:
            return False, f"连接失败: {e}"

    @_retry_on_rate_limit(
        max_retries=5,
        initial_delay=1.0,
        max_delay=300.0,
        backoff_factor=2.0,
        jitter=True,
    )
    def get_activities(
        self, start_date: datetime, end_date: Optional[datetime] = None
    ) -> List[Activity]:
        """获取指定日期范围内的活动"""
        self.rate_limiter.wait()
        if not self.client:
            raise RuntimeError("未登录，请先调用 login()")

        if end_date is None:
            end_date = datetime.now()

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        logger.info(f"获取 {start_str} 到 {end_str} 的活动")

        try:
            activities_data = self.client.get_activities_by_date(start_str, end_str)
            logger.info(f"获取到 {len(activities_data)} 条活动记录")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError(f"获取活动列表失败: {e}")
        except garth.exc.GarthHTTPError as e:
            if "429" in str(e):
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError(f"获取活动列表失败: {e}")
        except requests.exceptions.Timeout:
            raise GarminNetworkError("连接 Garmin 超时，请检查网络后重试")
        except (requests.exceptions.ConnectionError, socket.timeout):
            raise GarminNetworkError("无法连接 Garmin 服务器，请检查网络")
        except Exception as e:
            raise GarminAPIError(f"获取活动列表失败: {e}")

        activities = []
        for data in activities_data:
            try:
                activity = self._parse_activity(data)
                activities.append(activity)
            except Exception as e:
                logger.warning(f"解析活动失败: {e}")
                continue

        return activities

    @_retry_on_rate_limit(
        max_retries=5,
        initial_delay=1.0,
        max_delay=300.0,
        backoff_factor=2.0,
        jitter=True,
    )
    def get_activity_details(self, activity_id: int) -> dict:
        """获取活动详情（包含 GPS 轨迹等）"""
        self.rate_limiter.wait()
        if not self.client:
            raise RuntimeError("未登录，请先调用 login()")

        try:
            details = self.client.get_activity_details(activity_id)
            return details
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError(f"获取活动详情失败: {e}")
        except garth.exc.GarthHTTPError as e:
            if "429" in str(e):
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError(f"获取活动详情失败: {e}")
        except requests.exceptions.Timeout:
            raise GarminNetworkError("连接 Garmin 超时，请检查网络后重试")
        except (requests.exceptions.ConnectionError, socket.timeout):
            raise GarminNetworkError("无法连接 Garmin 服务器，请检查网络")
        except Exception as e:
            raise GarminAPIError(f"获取活动详情失败: {e}")

    @_retry_on_rate_limit(
        max_retries=5,
        initial_delay=1.0,
        max_delay=300.0,
        backoff_factor=2.0,
        jitter=True,
    )
    def get_activity_splits(self, activity_id: int) -> dict:
        """获取活动分段数据（每圈/每公里等）"""
        self.rate_limiter.wait()
        if not self.client:
            raise RuntimeError("未登录，请先调用 login()")

        try:
            splits = self.client.get_activity_splits(activity_id)
            return splits
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError(f"获取活动分段数据失败: {e}")
        except garth.exc.GarthHTTPError as e:
            if "429" in str(e):
                raise GarminAuthError("Garmin 服务器限流 (429)，请稍后再试")
            raise GarminAPIError(f"获取活动分段数据失败: {e}")
        except requests.exceptions.Timeout:
            raise GarminNetworkError("连接 Garmin 超时，请检查网络后重试")
        except (requests.exceptions.ConnectionError, socket.timeout):
            raise GarminNetworkError("无法连接 Garmin 服务器，请检查网络")
        except Exception as e:
            raise GarminAPIError(f"获取活动分段数据失败: {e}")

    def _parse_activity(self, data: dict) -> Activity:
        """解析活动数据"""
        start_time_str = data.get("startTimeLocal", "") or data.get("startTimeGMT", "")
        if start_time_str:
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        else:
            start_time = datetime.now()

        return Activity(
            activity_id=data.get("activityId", 0),
            activity_name=data.get("activityName", "Unknown"),
            activity_type=data.get("activityType", {}).get("typeKey", "unknown")
            if isinstance(data.get("activityType"), dict)
            else str(data.get("activityType", "unknown")),
            start_time=start_time,
            timezone=data.get("timeZoneId", ""),
            duration_seconds=data.get("duration", 0) or 0,
            distance_meters=data.get("distance", 0) or 0,
            avg_pace_seconds_per_km=(
                round(1000 / data["averageSpeed"]) if data.get("averageSpeed") else None
            ),
            avg_heartrate=data.get("averageHR"),
            max_heartrate=data.get("maxHR"),
            avg_cadence=data.get("averageCadence"),
            avg_power=data.get("averagePower"),
            elevation_gain=data.get("elevationGain"),
            elevation_loss=data.get("elevationLoss"),
            calories=data.get("calories"),
            avg_temperature=data.get("averageTemperature"),
            weather=data.get("weather"),
            raw_data=json.dumps(data, ensure_ascii=False),
        )

    def sync_recent_activities(self, days: int = 7) -> List[Activity]:
        """同步最近指定天数的活动

        大日期范围会拆分为多个月度请求，避免 API 超限。
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        if days <= MAX_DAYS_PER_REQUEST:
            return self.get_activities(start_date, end_date)

        # 拆分为月度请求
        all_activities = []
        current = start_date
        while current <= end_date:
            # 计算当前月结束日期
            _, last_day = monthrange(current.year, current.month)
            chunk_end = datetime(current.year, current.month, last_day)
            if chunk_end > end_date:
                chunk_end = end_date

            logger.info(f"获取 {current.strftime('%Y-%m-%d')} 到 {chunk_end.strftime('%Y-%m-%d')} 的活动")
            activities = self.get_activities(current, chunk_end)
            all_activities.extend(activities)

            # 移动到下个月
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)

        # 去重（按 activity_id）
        seen = set()
        unique = []
        for a in all_activities:
            if a.activity_id not in seen:
                seen.add(a.activity_id)
                unique.append(a)

        return unique
