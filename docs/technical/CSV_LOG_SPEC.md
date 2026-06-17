# CSV 日志文件规范

## 编码约定

所有 CSV 日志文件统一使用 **GBK** 编码。

**原因**：Windows 环境下 Excel 默认以 GBK（CP936）打开 CSV 文件，使用 UTF-8 会导致中文乱码。

**代码中的体现**：
```python
# 读取
with open(csv_file, 'r', encoding='gbk') as f:

# 写入
with open(csv_file, 'a', newline='', encoding='gbk') as f:
```

**注意事项**：
- 不要使用 `utf-8-sig`，虽然带 BOM 的 UTF-8 在部分 Excel 版本可识别，但与已有 GBK 数据混合会导致乱码
- 不要使用 `errors='ignore'`，这会静默丢失无法编码的字符，导致数据丢失
- **emoji 等字符处理**：GBK 无法编码 emoji，程序会在写入前自动过滤这些字符（替换为空字符串）
  - 原因：如 🚀、📱、🎉 等 emoji 会导致 `UnicodeEncodeError: 'gbk' codec can't encode character`
  - 解决：添加 `sanitize_for_gbk()` 函数，在写入 CSV 前过滤掉 Unicode 码点 >= 0x10000 的字符

## 文件清单

所有错误日志文件统一存放在 `log-err/` 目录下。

### 1. message_log.csv（主日志，位于 data/）

| 列名 | 说明 | 示例 |
|------|------|------|
| 标题 | 文章标题 | OpenClaw 发布了... |
| 日期 | 发表日期 YYYYMMDD | 20260214 |
| 星期 | 中文星期 | 周五 |
| 链接 | 文章URL（已截断追踪参数） | https://mp.weixin.qq.com/s/xxx |
| 来源 | 来源标识 | 微信-某公众号 |
| 标记 | Pin 等标记 | Pin |
| 是否重复 | 重复标记 | 重复 |
| 消息序号 | 飞书消息序号 | 283 |
| 摘录异常信息 | 解析异常说明 | 未获取标题 |
| 备注 | 预留 | |

### 2. duplicate_log.csv（重复记录日志，log-err/）

| 列名 | 说明 |
|------|------|
| 标题 | 文章标题 |
| 日期 | 发表日期 |
| 星期 | 中文星期 |
| 链接 | 文章URL |
| 来源 | 来源标识 |
| 标记 | Pin 等标记 |
| 消息序号 | 飞书消息序号 |
| 记录时间 | 写入日志的时间 YYYY/MM/DD HH:MM |

### 3. parse_error_log.csv（解析异常日志，log-err/）

| 列名 | 说明 |
|------|------|
| 标题 | 文章标题（可能为空） |
| 日期 | 发表日期（可能为空） |
| 星期 | 中文星期（可能为空） |
| 链接 | 原始URL |
| 来源 | 来源标识（可能为空） |
| 标记 | Pin 等标记 |
| 异常信息 | 具体异常描述 |
| 消息序号 | 飞书消息序号 |
| 记录时间 | 写入日志的时间 |

### 4. bitable_fail_log.csv（多维表格入库失败日志，log-err/）

| 列名 | 说明 |
|------|------|
| 标题 | 文章标题 |
| 日期 | 发表日期 |
| 星期 | 中文星期 |
| 链接 | 文章URL |
| 来源 | 来源标识 |
| 标记 | Pin 等标记 |
| 失败原因 | API 返回的错误信息 |
| 消息序号 | 飞书消息序号 |
| 记录时间 | 写入日志的时间 |

## 公共列约定

四个日志文件的前6列保持一致：

```
标题, 日期, 星期, 链接, 来源, 标记
```

各文件在此基础上追加各自的专属列，最后两列统一为：

```
消息序号, 记录时间（或备注等）
```

## WTA 缓存文件

### 5. wta_processed_urls.csv（WTA 本地缓存，位于 data/）

goWTA.py 使用的本地去重缓存，记录所有已写入多维表格的 URL。

**编码**：`utf-8-sig`（非 GBK，因为该文件不面向 Excel 用户，仅供程序读写）

| 列名 | 说明 | 示例 |
|------|------|------|
| id | 缓存序号（8位零填充） | 00000123 |
| url | 文章 URL（已截断参数） | https://arqiaoknow.feishu.cn/wiki/xxx |
| title | 文章标题 | AI 产品经理周报 |
| date | 日期 YYYYMMDD | 20260315 |
| record_id | 多维表格 record_id | recXXXXXX |
| parent_id | 父记录的 cache id（L2 指向 L1） | 00000120 |
| layer | 层级：`1`（WTA-1）或 `2`（WTA引用/orig） | 1 |
| 精选日期 | 所属精选合集的日期 | 20260314 |
| date_added | 写入缓存的时间 | 2026-03-15 10:30:00 |

**用途**：
- `url` + `layer` + `parent_id` 构成去重三元组（`dedup_keys`）
- `parent_id` 构建父子关系索引（`parent_children`），用于缓存子节点去重
- `id` 用于 `cache_id_to_norm` 反查和 `url_to_cache_id` 映射
- orig 类型的 L2 记录在 CSV 中 layer 为 `2`，与普通 L2 无法区分

## 历史问题记录

### 编码混乱事件（2026-02-18）

**起因**：goMessage.py 中 CSV 写入使用 `utf-8-sig` 编码，而已有数据文件为 GBK 编码，导致新写入的行与旧数据编码不一致。

**恶化**：修复脚本使用 `errors='ignore'` 转码，导致 duplicate_log.csv 数据全部丢失（17行→0行）；另外两个脚本向 parse_error_log.csv 和 bitable_fail_log.csv 插入了多余的空列。

**修复措施**：
1. goMessage.py 所有 CSV 读写统一为 `encoding='gbk'`
2. message_log.csv 第294-370行从 UTF-8 逐行转为 GBK
3. parse_error_log.csv 删除3个多余空列
4. bitable_fail_log.csv 删除1个多余空列
5. duplicate_log.csv 从 message_log.csv 按 URL 匹配重建

**教训**：
- 修改编码前必须确认文件当前的实际编码
- 不要使用 `errors='ignore'` 做批量转码
- 修改文件前先备份
