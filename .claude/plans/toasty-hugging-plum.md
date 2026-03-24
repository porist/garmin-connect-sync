# Plan: 修正获取活动详情数据超时问题

## Context

获取活动详情（GPS轨迹等大数据量）时，默认 10 秒超时太短，导致请求失败。`garth` 库硬编码了 10 秒超时，而 `garminconnect` 库的 `Garmin` 类没有暴露 timeout 参数。

**根本原因**:
- `garth.Client.timeout = 10` (garth/http.py:27)
- `Garmin.__init__` 不接受 timeout 参数
- 应用层无法配置超时时间

## 解决方案

在登录后通过 `garth.Client.configure(timeout=xxx)` 设置超时时间。

### 修改文件

**1. `config.yaml`** - 添加 timeout 配置项
```yaml
garmin:
  email: "xxx"
  password: "xxx"
  timeout: 30  # 新增：请求超时秒数（默认 10）
```

**2. `app/config.py`** - 添加 `garmin_timeout` 属性
```python
@property
def garmin_timeout(self) -> int:
    return self._data.get("garmin", {}).get("timeout", 10)
```

**3. `app/garmin_client.py`** - 登录后设置超时
```python
def login(self):
    # ... 现有登录代码 ...
    # 登录后设置超时
    if hasattr(self.client, 'garth'):
        self.client.garth.configure(timeout=self.config.garmin_timeout)
```

## 验证方式

1. 临时将 timeout 设为极小值（如 1），触发超时错误
2. 观察日志中错误信息变为 `requests.exceptions.ReadTimeout`
3. 恢复正常值后，验证活动详情能成功获取
