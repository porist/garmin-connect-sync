import json
import logging
import socket
import time
from calendar import monthrange
from datetime import datetime, timedelta
from functools import wraps
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

# 429 限流退避策略默认参数
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_DELAY = 1.0
DEFAULT_MAX_DELAY = 300.0
DEFAULT_BACKOFF_FACTOR = 2.0


def _retry_on_rate_limit(
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
):
    """429 限流退避重试装饰器

    当遇到 GarminAuthError (429) 时，使用指数退避策略重试。
    超过最大重试次数后返回 None。
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

    @_retry_on_rate_limit()
    def login(self):
        """登录 Garmin Connect 账号"""
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

    @_retry_on_rate_limit()
    def get_activities(
        self, start_date: datetime, end_date: Optional[datetime] = None
    ) -> List[Activity]:
        """获取指定日期范围内的活动"""
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

    @_retry_on_rate_limit()
    def get_activity_details(self, activity_id: int) -> dict:
        """获取活动详情（包含 GPS 轨迹等）"""
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

    @_retry_on_rate_limit()
    def get_activity_splits(self, activity_id: int) -> dict:
        """获取活动分段数据（每圈/每公里等）"""
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
