# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本代码仓库中工作时提供指导。

## 项目概述

Garmin Connect 数据同步应用。从 Garmin Connect 国际 API 获取运动活动记录（跑步、骑行、游泳等）并本地存储。

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
```

### 配置
将 `config.yaml.example` 复制为 `config.yaml`，填入你的 Garmin Connect 凭据。

### 运行操作
```bash
# 同步一次活动数据
python app/main.py --sync

# 启动定时同步守护进程（默认每6小时执行一次）
python app/main.py --daemon

# 查看最近的活动
python app/main.py --list --limit 20

# 查看同步历史
python app/main.py --list-syncs
```

### 开发
- Python 3.10+
- 使用 `garminconnect` 库连接 Garmin Connect API
- SQLite 本地数据存储

## 架构

```
app/
├── main.py           # CLI 入口
├── config.py         # YAML 配置加载器
├── garmin_client.py  # Garmin Connect API 封装
├── models.py         # 活动数据模型
├── storage.py        # SQLite 存储层
└── scheduler.py      # APScheduler 定时任务调度器
```

## 核心组件

- `GarminClient`：封装 garminconnect 库，处理登录/认证、数据获取
- `Storage`：基于 SQLite 的活动数据和同步历史持久化
- `Config`：YAML 配置加载器（单例模式）
- `SyncScheduler`：通过 APScheduler 管理定期同步任务
