#!/usr/bin/env python3
"""
Garmin Connect 数据同步应用
Usage:
    python app/main.py --sync              # 执行一次同步
    python app/main.py --daemon           # 启动定时同步守护进程
    python app/main.py --list             # 列出最近的活动
    python app/main.py --list-syncs       # 列出最近的同步记录
    python app/main.py --pending-details # 列出尚未获取详情的活动
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from app.config import Config
from app.export import export_activities_xls
from app.garmin_client import GarminAPIError, GarminAuthError, GarminClient, GarminNetworkError
from app.scheduler import SyncScheduler
from app.storage import Storage

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _fetch_single_activity_details(client, activity_id, max_retries=3):
    """获取单个活动详情，支持重试"""
    delays = [1, 5, 15]  # 指数退避
    last_error = None

    for attempt in range(max_retries):
        try:
            details = client.get_activity_details(activity_id)
            splits = client.get_activity_splits(activity_id)
            return details, splits
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(delays[attempt])
    raise last_error


def sync_once(config: Config, storage: Storage, days: int = 7, fetch_details: bool = True):
    """执行一次同步"""
    client = GarminClient(config)
    try:
        client.login()
    except GarminAuthError as e:
        print(f"\n同步失败: {e}")
        return
    except GarminNetworkError as e:
        print(f"\n同步失败: {e}")
        return
    except GarminAPIError as e:
        print(f"\n同步失败: {e}")
        return
    except Exception as e:
        print(f"\n同步失败: {e}")
        return

    try:
        activities = client.sync_recent_activities(days=days)
        fetched, new = storage.save_activities(activities)
        logger.info(f"同步完成: 获取 {fetched} 条, 新增 {new} 条")

        # Phase 2: 获取详情
        details_fetched = 0
        details_failed = 0
        failed_ids = []

        if fetch_details:
            activity_ids = storage.get_activities_without_details(limit=100)
            if activity_ids:
                print(f"\n正在获取 {len(activity_ids)} 个活动的详情...")
                iterator = tqdm(activity_ids, desc="获取详情") if tqdm else activity_ids
                for activity_id in iterator:
                    try:
                        details, splits = _fetch_single_activity_details(client, activity_id)
                        storage.save_activity_details(activity_id, details, splits)
                        details_fetched += 1
                    except Exception as e:
                        logger.warning(f"获取详情失败 (ID: {activity_id}): {e}")
                        details_failed += 1
                        failed_ids.append(activity_id)

                if failed_ids:
                    print(f"详情获取成功: {details_fetched}, 失败: {details_failed} (ID: {', '.join(map(str, failed_ids[:5]))})")
                else:
                    print(f"详情获取完成: {details_fetched} 个")

        storage.log_sync(fetched, new, "success", details_fetched=details_fetched, details_failed=details_failed)

        # 显示活动摘要
        if activities:
            print("\n最近的活动:")
            for a in activities[:5]:
                print(
                    f"  - {a.start_time.date()} | {a.activity_type:12} | "
                    f"{a.distance_km:.2f} km | {a.activity_name}"
                )
    finally:
        client.logout()


def list_activities(storage: Storage, limit: int = 20):
    """列出最近的活动"""
    activities = storage.get_activities(limit=limit)
    if not activities:
        print("本地数据库为空，请先运行 ./garmin sync 获取数据")
        return

    # 检查最后一条活动是否是今天
    last_activity = activities[0]  # 已按时间倒序
    today = datetime.now().date()
    if last_activity.start_time.date() != today:
        print(f"数据可能过期（最后记录: {last_activity.start_time.date()}），如需最新数据请运行 ./garmin sync")
        print()

    print(f"\n最近 {len(activities)} 条活动:")
    for a in activities:
        print(
            f"  [{a.activity_id}] {a.start_time.date()} | {a.activity_type:12} | "
            f"{a.distance_km:.2f} km | {a.activity_name}"
        )


def list_syncs(storage: Storage, limit: int = 10):
    """列出最近的同步记录"""
    syncs = storage.get_recent_syncs(limit=limit)
    if not syncs:
        print("没有同步记录")
        return

    print(f"\n最近 {len(syncs)} 次同步:")
    for s in syncs:
        print(
            f"  {s['sync_time'][:19]} | 获取: {s['activities_fetched']:3} | "
            f"新增: {s['activities_new']:3} | {s['status']}"
        )


def list_activities_without_details(storage: Storage, limit: int = 100):
    """列出尚未获取详情的活动"""
    activity_ids = storage.get_activities_without_details(limit=limit)
    if not activity_ids:
        print("所有活动都已获取详情")
        return

    activities = storage.get_activities(limit=1000)
    pending_ids_set = set(activity_ids)
    pending = [a for a in activities if a.activity_id in pending_ids_set]

    print(f"\n尚未获取详情的活动 ({len(pending)} 条):")
    for a in pending:
        print(
            f"  [{a.activity_id}] {a.start_time.date()} | {a.activity_type:12} | "
            f"{a.distance_km:.2f} km | {a.activity_name}"
        )


def show_activity_detail(config: Config, activity_id: int):
    """显示指定活动的详细信息和分段数据"""
    storage = Storage(config.database_path)

    # 先从本地数据库获取活动基本信息
    activities = storage.get_activities(limit=1000)
    activity = next((a for a in activities if a.activity_id == activity_id), None)

    if not activity:
        print(f"未找到活动 ID: {activity_id}")
        return

    print(f"\n{'='*60}")
    print(f"活动详情 - {activity.activity_name}")
    print(f"{'='*60}")
    print(f"  活动 ID:      {activity.activity_id}")
    print(f"  活动类型:     {activity.activity_type}")
    print(f"  开始时间:     {activity.start_time}")
    print(f"  时区:         {activity.timezone}")
    print(f"  距离:         {activity.distance_km:.2f} km")
    print(f"  时长:         {activity.duration_minutes:.1f} 分钟")
    if activity.avg_pace_seconds_per_km:
        pace_min = int(activity.avg_pace_seconds_per_km // 60)
        pace_sec = int(activity.avg_pace_seconds_per_km % 60)
        print(f"  平均配速:     {pace_min}:{pace_sec:02d} /km")
    if activity.avg_heartrate:
        print(f"  平均心率:     {activity.avg_heartrate} bpm")
    if activity.max_heartrate:
        print(f"  最大心率:     {activity.max_heartrate} bpm")
    if activity.avg_cadence:
        print(f"  平均步频:     {activity.avg_cadence:.1f}")
    if activity.avg_power:
        print(f"  平均功率:     {activity.avg_power:.1f} W")
    if activity.elevation_gain:
        print(f"  爬升:         {activity.elevation_gain:.1f} m")
    if activity.elevation_loss:
        print(f"  下降:         {activity.elevation_loss:.1f} m")
    if activity.calories:
        print(f"  热量:         {activity.calories} kcal")
    if activity.weather:
        print(f"  天气:         {activity.weather}")

    # 优先从本地数据库获取详情
    details = None
    splits = None
    from_local = False

    cached = storage.get_activity_details(activity_id)
    if cached:
        details, splits = cached
        from_local = True
        print(f"\n  (详情数据来源: 本地缓存)")

    if not details:
        # 从 API 获取详细和分段数据
        client = GarminClient(config)
        try:
            client.login()
        except GarminAuthError as e:
            print(f"\n详情获取失败: {e}")
            return
        except GarminNetworkError as e:
            print(f"\n详情获取失败: {e}")
            return
        except GarminAPIError as e:
            print(f"\n详情获取失败: {e}")
            return

        try:
            details = client.get_activity_details(activity_id)
            splits = client.get_activity_splits(activity_id)
            client.logout()
            # 保存到本地
            storage.save_activity_details(activity_id, details, splits)
            print(f"\n  (详情数据来源: API实时获取)")
        except GarminAuthError as e:
            print(f"\n详情获取失败: {e}")
            return
        except GarminNetworkError as e:
            print(f"\n详情获取失败: {e}")
            return
        except GarminAPIError as e:
            print(f"\n详情获取失败: {e}")
            return
        except Exception as e:
            logger.error(f"获取活动详情失败: {e}")
            print(f"\n详情获取失败: {e}")
            return

    # 打印圈数据 (来自 splits 中的 lapDTOs)
    lap_dtos = splits.get("lapDTOs", []) if splits else []
    if lap_dtos:
        print(f"\n{'-'*60}")
        print("圈数据 (Laps)")
        print(f"{'-'*60}")
        for lap in lap_dtos:
            distance = lap.get("distance", 0) or 0
            elapsed_time = lap.get("elapsedDuration", 0) or lap.get("duration", 0) or 0
            hr = lap.get("averageHR", "") or ""

            # 计算配速 (min/km)
            pace_str = ""
            if distance > 0 and elapsed_time > 0:
                pace_sec = (elapsed_time / distance) * 1000  # 转换为秒/公里
                pace_min = int(pace_sec // 60)
                pace_sec_int = int(pace_sec % 60)
                pace_str = f"{pace_min}:{pace_sec_int:02d}"

            lap_idx = lap.get("lapIndex", "?")
            intensity = lap.get("intensityType", "")
            print(f"  圈{lap_idx:2} | {distance:7.1f}m | {elapsed_time:>8.1f}s | 配速: {pace_str:>8}/km | 心率: {hr} | {intensity}")


def print_help():
    """显示使用说明"""
    help_text = """
╔══════════════════════════════════════════════════════════════════╗
║              Garmin Connect 数据同步工具                         ║
╚══════════════════════════════════════════════════════════════════╝

  --sync              执行一次同步（默认最近7天）
  --daemon            启动定时同步守护进程
  --list              列出本地活动记录
  --list-syncs        列出同步历史
  --pending-details   列出缺少详情的活动
  --detail <id>       显示活动详情和分段数据
  --ping              测试 API 连通性

参数:
  --days N            同步最近 N 天（默认: 7）
  --limit N           列表显示数量（默认: 20）
  --no-details        同步时不获取详情数据
"""
    print(help_text)


def ping_server(config: Config):
    """测试 API 连通性"""
    client = GarminClient(config)
    print("正在测试 Garmin Connect 连接...", end=" ", flush=True)
    success, message = client.ping()
    if success:
        print(f"✓ {message}")
    else:
        print(f"✗ {message}")


def handle_config_cmd(config: Config, args: list):
    """处理配置命令"""
    if not args:
        # 显示所有配置
        data = config.get_all()
        print("\n当前配置:")
        print(f"  garmin.email: {data.get('garmin', {}).get('email', '未设置')}")
        print(f"  garmin.timeout: {data.get('garmin', {}).get('timeout', 10)}")
        print(f"  database.path: {data.get('database', {}).get('path', 'data/garmin.db')}")
        print(f"  scheduler.sync_interval_hours: {data.get('scheduler', {}).get('sync_interval_hours', 6)}")
        print(f"  scheduler.cron: {data.get('scheduler', {}).get('cron', '未设置')}")
        print()
        return

    key = args[0]
    if len(args) == 1:
        # 显示指定配置项
        value = config.get(key)
        if value is None:
            print(f"错误: 未找到配置项 '{key}'")
        else:
            print(f"{key} = {value}")
    elif len(args) == 2:
        # 设置配置项
        value = args[1]
        # 类型转换
        if key == "garmin.timeout" or key == "scheduler.sync_interval_hours":
            value = int(value)
        config.set(key, value)
        config.save()
        print(f"已设置 {key} = {value}")
    else:
        print("用法: config [key] [value]")
        print("  config              显示所有配置")
        print("  config <key>        显示指定配置")
        print("  config <key> <val>  修改配置")


def main():
    parser = argparse.ArgumentParser(description="Garmin Connect 数据同步工具")
    parser.add_argument(
        "--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument("--sync", action="store_true", help="执行一次同步")
    parser.add_argument("--daemon", action="store_true", help="启动定时同步守护进程")
    parser.add_argument(
        "--list", action="store_true", help="列出最近的活动"
    )
    parser.add_argument(
        "--list-syncs", action="store_true", help="列出最近的同步记录"
    )
    parser.add_argument(
        "--pending-details", action="store_true", help="列出尚未获取详情的活动"
    )
    parser.add_argument(
        "--detail", type=int, metavar="ACTIVITY_ID", help="显示指定活动的详细信息和分段数据"
    )
    parser.add_argument(
        "--ping", action="store_true", help="测试 API 连通性"
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="列出活动的数量 (默认: 20)"
    )
    parser.add_argument(
        "--days", type=int, default=7, help="同步最近天数 (默认: 7)"
    )
    parser.add_argument(
        "--no-details", action="store_true", help="同步时不获取详情数据"
    )
    parser.add_argument(
        "--info", action="store_true", help="显示详细帮助信息"
    )
    parser.add_argument(
        "--config-cmd", nargs="*", help="配置命令: config [key] [value]"
    )
    parser.add_argument(
        "--export", metavar="FILE", help="导出活动为 XLS 文件"
    )

    args = parser.parse_args()

    if args.info:
        print_help()
        sys.exit(0)

    try:
        config = Config.get_instance(args.config)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请复制 config.yaml.example 为 config.yaml 并配置您的 Garmin 账号信息")
        sys.exit(1)

    storage = Storage(config.database_path)

    if args.daemon:
        scheduler = SyncScheduler(config, storage)
        try:
            scheduler.start()
        except KeyboardInterrupt:
            scheduler.stop()
            print("\n守护进程已停止")
    elif args.sync:
        sync_once(config, storage, days=args.days, fetch_details=not args.no_details)
    elif args.list:
        list_activities(storage, args.limit)
    elif args.list_syncs:
        list_syncs(storage)
    elif args.pending_details:
        list_activities_without_details(storage, args.limit)
    elif args.detail is not None:
        show_activity_detail(config, args.detail)
    elif args.ping:
        ping_server(config)
    elif args.config_cmd is not None:
        handle_config_cmd(config, args.config_cmd)
    elif args.export:
        activities = storage.get_activities(limit=1000)
        if not activities:
            print("没有活动数据可导出")
        else:
            export_activities_xls(activities, args.export)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
