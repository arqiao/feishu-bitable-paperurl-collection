# 飞书 Token 管理说明

## Token 类型和有效期

### 1. user_access_token（用户访问令牌）
- **有效期**: 2 小时
- **用途**: 以用户身份调用 API（读取消息、撤回消息等）
- **刷新方式**: 使用 refresh_token 刷新

### 2. user_refresh_token（用户刷新令牌）
- **有效期**: 30 天
- **用途**: 刷新 user_access_token
- **刷新方式**: 每次刷新 access_token 时会同时获得新的 refresh_token

### 3. app_access_token（应用访问令牌）
- **有效期**: 2 小时
- **用途**: 以应用身份调用 API
- **获取方式**: 使用 app_id 和 app_secret 获取

## Token 过期原因

### 为什么 Token 过期这么快？

**这是飞书的安全设计：**

1. **短期 access_token（2小时）**
   - 降低 token 泄露风险
   - 即使被盗用，影响时间有限
   - 符合 OAuth 2.0 标准实践

2. **长期 refresh_token（30天）**
   - 避免频繁重新授权
   - 可以自动刷新 access_token
   - 30天内无需用户干预

## 自动刷新机制

程序有两层自动刷新：

### 第一层：启动时检查

程序启动时检查 token 是否过期（提前 5 分钟判断）：

```python
if not client.check_token_valid():
    if not client.refresh_access_token():
        print("请运行: python src/auth.py")
```

### 第二层：API 调用中自动刷新

所有 API 调用通过统一的 `_request()` 方法发出。当 API 返回 token 过期错误码时，自动刷新并重试：

```python
TOKEN_EXPIRED_CODES = {99991677, 99991668, 99991664}

def _request(self, method, endpoint, use_user_token=True, max_retries=3, ...):
    for attempt in range(max_retries):
        response = requests.request(method, url, ...)
        result = response.json()
        if result.get('code') in self.TOKEN_EXPIRED_CODES:
            self.refresh_access_token()
            continue  # 刷新后重试
        ...
```

这解决了长时间运行（处理大量消息）时 token 在中途过期的问题。

### 刷新流程

```
运行程序
    ↓
检查 token 是否过期（提前5分钟）
    ↓
    ├─→ 未过期 → 继续执行
    │
    └─→ 已过期 → 尝试刷新
            ↓
            ├─→ 刷新成功 → 继续执行
            │
            └─→ 刷新失败 → 提示重新授权
```

## 更持久的解决方案

### 方案 1：定期自动刷新（推荐）

创建一个定时任务，每天运行一次程序，自动刷新 token：

**Windows 任务计划程序：**
```
触发器: 每天运行一次
操作: python D:\path\to\goMessage.py
```

**Linux/Mac crontab：**
```bash
# 每天凌晨 2 点运行
0 2 * * * cd /path/to/project && python src/goMessage.py
```

**优点：**
- refresh_token 在 30 天内保持有效
- 无需频繁手动授权
- 自动处理新消息

### 方案 2：使用应用令牌（适用于特定场景）

如果只需要读取消息（不需要以用户身份操作），可以使用应用令牌：

```python
# 使用应用身份获取 tenant_access_token
def get_tenant_access_token(self) -> str:
    url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
    data = {
        "app_id": self.config['feishu']['app_id'],
        "app_secret": self.config['feishu']['app_secret']
    }
    response = requests.post(url, json=data)
    result = response.json()
    return result['tenant_access_token']
```

**限制：**
- 无法撤回其他用户的消息
- 某些用户权限的操作无法执行

### 方案 3：Token 监控和预警

添加 token 过期监控：

```python
def check_token_status(self):
    """检查 token 状态并预警"""
    expire_time = self.config['auth'].get('user_token_expire_time', 0)
    current_time = int(time.time())

    # 计算剩余时间
    remaining_hours = (expire_time - current_time) / 3600

    if remaining_hours < 1:
        print(f"⚠️  警告: access_token 将在 {remaining_hours:.1f} 小时后过期")

    # 检查 refresh_token（假设30天有效期）
    last_auth_time = self.config['auth'].get('last_auth_time', 0)
    refresh_remaining_days = 30 - (current_time - last_auth_time) / 86400

    if refresh_remaining_days < 7:
        print(f"⚠️  警告: refresh_token 将在 {refresh_remaining_days:.0f} 天后过期，建议重新授权")
```

## 常见问题

### Q1: 为什么刷新失败提示 "missing app id or app secret"？

**原因：**
- 刷新 API 请求体中缺少 `app_id` 或 `app_secret` 参数
- 或者 config.yaml 中缺少这些配置

**解决方法：**
1. 确保 config.yaml 包含正确的应用凭证：
```yaml
feishu:
  app_id: "cli_xxxxx"
  app_secret: "xxxxx"
```

2. 确保刷新 API 调用时在请求体中包含这两个参数（不是在 Header 中）

> **踩坑记录**：飞书文档中 refresh_access_token 接口的示例可能不完整，实际调用时必须在请求体中同时传入 `grant_type`、`refresh_token`、`app_id`、`app_secret` 四个参数。

### Q2: 每次运行都要重新授权吗？

**不需要！** 正常情况下：
- 30 天内只需授权一次
- 程序会自动刷新 access_token
- 只有 refresh_token 过期才需要重新授权

### Q3: 如何避免频繁授权？

**最佳实践：**

1. **定期运行程序**（推荐每天一次）
   - 保持 refresh_token 活跃
   - 自动处理新消息

2. **不要长时间不使用**
   - 超过 30 天不运行，refresh_token 会过期
   - 需要重新授权

3. **保护 config.yaml**
   - 不要删除或修改 auth 部分
   - 不要提交到版本控制

### Q4: refresh_token 过期了怎么办？

**只能重新授权：**
```bash
python src/auth.py
```

**refresh_token 过期的原因：**
- 超过 30 天未使用
- 用户在飞书中撤销了授权
- 应用权限发生变更

### Q5: 可以延长 token 有效期吗？

**不可以。** 这是飞书平台的限制：
- access_token: 固定 2 小时
- refresh_token: 固定 30 天

**但可以通过自动刷新机制实现"永久"使用：**
- 只要定期运行程序（30天内至少一次）
- refresh_token 会自动更新
- 无需手动干预

## 推荐配置

### 日常使用（推荐）

**设置定时任务，每天运行一次：**

```bash
# Linux/Mac
0 2 * * * cd /path/to/project && python src/goMessage.py >> /path/to/log.txt 2>&1

# Windows 任务计划程序
# 每天凌晨 2:00 运行 python src/goMessage.py
```

**优点：**
- 自动处理新消息
- 自动刷新 token
- 30 天内无需重新授权

### 手动使用

**每次使用前检查：**
```bash
python src/goMessage.py
```

**如果提示重新授权：**
```bash
python src/auth.py
```

## Token 刷新日志

程序会显示详细的刷新信息：

```
Token 已过期或无效，尝试刷新...
✓ Token 刷新成功，有效期: 2.0 小时
```

或者：

```
Token 已过期或无效，尝试刷新...
刷新 token 失败 (code: 10012): refresh_token invalid
提示: refresh_token 已过期，需要重新授权

✗ Token 刷新失败，请重新授权：
  运行命令: python src/auth.py
```

## 技术细节

### Token 存储

Token 存储在 `config.yaml` 中：

```yaml
auth:
  user_access_token: "u-xxxxx"      # 2小时有效
  user_refresh_token: "ur-xxxxx"    # 30天有效
  user_token_expire_time: 1234567890  # 过期时间戳
```

### 刷新 API

```http
POST https://open.feishu.cn/open-apis/authen/v1/refresh_access_token
Content-Type: application/json

{
  "grant_type": "refresh_token",
  "refresh_token": "ur-xxxxx",
  "app_id": "cli_xxxxx",
  "app_secret": "xxxxx"
}
```

> **注意**：`app_id` 和 `app_secret` 是必需参数，缺少会返回错误码 20025。

### 响应

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "access_token": "u-xxxxx",
    "refresh_token": "ur-xxxxx",
    "expires_in": 7200,
    "token_type": "Bearer"
  }
}
```

## 总结

1. **Token 2小时过期是正常的**，这是安全设计
2. **程序会自动刷新**，无需手动干预
3. **30天内至少运行一次**，保持 refresh_token 有效
4. **推荐设置定时任务**，每天自动运行
5. **只有 refresh_token 过期才需要重新授权**

**最佳实践：**
- ✅ 设置定时任务，每天运行一次
- ✅ 保护好 config.yaml 文件
- ✅ 不要长时间（超过30天）不使用
- ❌ 不要频繁删除 config.yaml
- ❌ 不要手动修改 auth 部分
