# Garmin Connect 数据同步工具

从 Garmin Connect 获取运动活动记录并本地存储。

## 使用流程

1. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

2. **配置账号**
   ```bash
   ./garmin config garmin.email "your@email.com"
   ./garmin config garmin.password "your_password"
   ```

3. **首次同步**
   ```bash
   ./garmin sync
   ```

4. **日常使用**
   ```bash
   ./garmin list              # 查看活动
   ./garmin export            # 导出数据
   ./garmin daemon            # 开启定时同步
   ```

## 命令

| 命令 | 说明 |
|------|------|
| `sync [天数]` | 同步活动数据 |
| `daemon` | 启动定时同步守护进程 |
| `list [数量]` | 列出活动记录 |
| `syncs` | 查看同步历史 |
| `detail <id>` | 显示活动详情 |
| `export [文件名]` | 导出为 XLS |
| `config [key [value]]` | 查看或修改配置 |
| `ping` | 测试 API 连接 |
| `help` | 显示帮助 |
