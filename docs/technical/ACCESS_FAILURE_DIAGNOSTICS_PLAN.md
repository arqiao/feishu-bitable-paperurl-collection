# 访问故障诊断规范与推进台账

本文档用于沉淀“外部服务访问失败时如何判断、提示、复用和逐步改造”的工作规范。后续 session 如果修改涉及 HTTP/API 访问、Token、权限、限流、重试或下载流程，应优先参考本文档，并同步更新推进台账。

## 目标

- 终端回显能区分“最终失败”“曾经失败但重试成功”“没有数据”“因权限或 Token 无法访问”。
- 访问失败时给出可操作原因，尽量指向用户下一步应检查的位置。
- 把通用判断沉淀为公共模块，但不把不同平台的个性化提示强行混在一起。
- 每次改造都能被测试验证，并在本文档记录进度。

## 适用范围

适用于所有访问外部服务的代码，包括但不限于：

- 知识星球：帖子列表、附件下载地址、知识星球文章解析。
- 飞书：消息、文档、多维表格、Token 刷新。
- 微信公众号、微博、小红书、WaytoAGI、LLM API 等网络访问。

不适用于纯本地文件处理、数据格式转换、无网络依赖的逻辑。

## 分层原则

### 1. 公共诊断层

公共模块只负责结构化判断，不直接打印用户文案。

建议模块：

```text
src/modules/http_diagnostics.py
```

建议职责：

- 将 HTTP 状态码、请求异常、响应解析异常归类。
- 给出是否可重试的建议。
- 保留原始状态码、服务错误码、简短错误信息。

建议基础分类：

| 分类 | 典型情况 | 默认重试 |
| --- | --- | --- |
| `auth` | 401、Token 失效、未登录 | 否 |
| `permission` | 403、无群组/文档/资源权限 | 否 |
| `rate_limit` | 429、服务限流 | 是 |
| `server` | 5xx、服务端异常 | 是 |
| `timeout` | 请求超时 | 是 |
| `network` | DNS、连接失败、连接重置 | 是 |
| `bad_response` | 非 JSON、响应结构缺字段 | 视场景 |
| `unknown` | 无法归类 | 是 |

### 2. 平台适配层

平台适配层负责把公共诊断结果翻译成该平台的具体含义。

建议模块示例：

```text
src/modules/zsxq_client.py
src/modules/feishu_errors.py
```

知识星球示例：

- `auth`：提示检查 `~/.config/secrets/gtokens.yaml` 中的 `zsxq.access_token`。
- `permission`：提示确认账号是否仍在对应星球内，或群组 ID 是否正确。
- `rate_limit`：提示稍后重试，并保留自动重试。

飞书示例：

- Token 过期：优先走现有自动刷新逻辑。
- refresh_token 失效：提示重新运行授权流程。
- 文档无权限：提示检查文档分享权限或应用权限。

### 3. 调用方展示层

主程序负责把“正在做什么”和“失败影响”讲清楚。

调用方必须说明：

- 当前操作对象，例如群组、文档、URL、下载文件。
- 失败发生在哪一步，例如列表页、详情页、下载地址、文件保存。
- 本次是否继续处理、跳过当前对象、还是整体中止。
- 状态文件是否更新，以及为什么更新或不更新。

## 终端提示规范

### 重试过程

单次失败但仍会重试时，提示应包含：

- 第几次尝试失败。
- 失败原因摘要。
- 下一次重试等待时间。

示例：

```text
  第 1 页获取失败（第 1/5 次）：请求超时，2 秒后重试
```

### 重试后成功

如果曾经失败但最终成功，必须明确提示成功恢复。

```text
  第 1 页重试成功（第 3/5 次）
```

### 最终失败

最终失败时不能再用“无数据”作为结论，应说明结果作废或处理中止。

```text
  第 1 页最终获取失败：知识星球认证失败，请更新 ~/.config/secrets/gtokens.yaml 中的 zsxq.access_token
  本群组处理失败：帖子列表未完整获取，本次结果作废
  last_download_date 未更新，原因：帖子列表抓取失败
```

### 正常无数据

只有完整访问成功后，才允许输出“没有发现可下载文件”。

```text
  翻页完成，共 1 页
找到 0 个含文件的帖子
无文件需要下载
last_download_date 未更新，原因：本次没有发现可下载文件
```

## 重试策略规范

- 认证失败、权限失败默认不重试，避免浪费时间和误导用户。
- 限流、超时、网络波动、5xx 可以重试。
- 文件下载和列表读取可以有不同重试次数，但提示格式应一致。
- 不确定是否安全的写操作不要直接套用公共重试器。

## 变更流程

每次改造按以下步骤推进：

1. 明确本次只改哪个调用点或平台。
2. 先写或补测试，覆盖失败分类、重试成功、最终失败提示。
3. 实现最小代码改动。
4. 运行聚焦测试和相关全量测试。
5. 更新本文档的推进台账。

## 推进台账

状态说明：

- `done`：已实现并通过测试。
- `next`：建议下一步优先做。
- `todo`：待做，尚未开始。
- `blocked`：需要用户凭证、外部权限或设计确认。

| 状态 | 项目 | 范围 | 验证方式 | 备注 |
| --- | --- | --- | --- | --- |
| done | `dfZSXQ.py` 区分最终失败和正常无数据 | 知识星球帖子列表、下载地址重试提示 | `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'` | 已避免“失败后看起来像无数据”的误导 |
| done | `dfZSXQ.py` 接入明确访问故障提示 | 知识星球列表页、下载地址 | `python -m compileall -q src/dfZSXQ.py tests/test_dfzsxq_output.py`；`PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'` | 已覆盖 401/403/429/1059/5xx/timeout；401/403 不继续重试 |
| done | `goAIPM.py --update` 与 `goWTA.py --update` 输出优化 | AIPM 知识星球日报查找、WTA 增量处理状态提示 | `python -m compileall -q src/goAIPM.py src/goWTA.py tests/test_update_output_messages.py`；`PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'` | AIPM 明确重试失败/恢复和一基页码；WTA 明确无 URL 与写入失败时不推进状态 |
| done | `goWXGZH.py --update` 输出优化 | 微信公众号列表抓取、文章解析重试、无文章状态提示 | `python -m compileall -q src/goWXGZH.py tests/test_gowxgzh_output.py`；`PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'` | 明确解析失败原因、重试恢复、列表抓取不完整时不推进状态 |
| done | `goAIPM.py --towiki` 安全重试与输出优化 | 网页/PDF 读取、图片下载、飞书文档整份重建、认证与权限提示 | `PYTHONPATH=src python -m unittest tests.test_towiki_output`；两条实际 `--towiki` 命令 | 只读请求直接重试；结果未知的写请求不原地重发，失败后重新清空并整份写入；Docling 预检并明确报告 PyMuPDF 回退成功 |
| done | `goMessage.py --profile` 汇总输出优化 | profile、解析异常、CSV、Bitable、撤回及状态推进 | `PYTHONPATH=src python -m unittest tests.test_gomessage_output`；`python src/goMessage.py --profile ai` | 解析异常不再计入解析成功，保持原有写日志、撤回及状态策略 |
| next | 增加公共 HTTP 诊断模型 | `src/modules/http_diagnostics.py` | 新增单元测试覆盖 401/403/429/5xx/timeout/network | 只做结构化分类，不直接打印 |
| todo | 抽出知识星球平台适配 | `src/modules/zsxq_client.py` 或等价小模块 | 覆盖 token 缺失、token 失效、无权限、限流 | 先服务 `dfZSXQ.py`，再考虑 `goAIPM.py` |
| todo | 对齐 `goAIPM.py` 的知识星球访问提示 | 知识星球文章解析/周报处理 | 现有测试 + 新增认证失败测试 | 复用平台适配层，避免两套文案 |
| todo | 梳理飞书访问故障提示 | `src/feishu_client.py` 及调用方 | 飞书 token/权限/限流测试 | 保留现有自动刷新逻辑 |
| todo | 梳理微信/微博/小红书等网页访问提示 | URL 解析器及对应模块 | 针对性解析失败测试 | 登录态和页面结构变化应分开提示 |

## 复盘节奏

- 每完成一个 `next` 或 `todo` 项，更新推进台账状态和备注。
- 每次修改外部访问逻辑前，先检查本文档是否已有相关条目。
- 每 3 个相关改动后做一次小复盘：删除已过时条目，补充遗漏场景，确认公共模块没有过度抽象。
- 如果某个失败类型连续出现两次以上但无法清晰提示，应新增台账项。

## 当前已知判断

- 知识星球 `401` 更可能是 `zsxq.access_token` 失效或未登录，不是下载目录配置问题。
- 知识星球凭证当前从本机密钥配置加载，重点检查 `~/.config/secrets/gtokens.yaml`。
- 飞书 Token 管理已有独立说明，见 `docs/technical/TOKEN_MANAGEMENT.md`。
- 当前 Mac 环境的 `NO_PROXY` 含裸 IPv6 条目 `::1`，Docling 使用的
  `httpx` 会将其误解析为端口；`goAIPM.py --towiki` 会在调用 Docling
  前预检并明确回退到 PyMuPDF，避免遗留未关闭的 PDFium 对象。
