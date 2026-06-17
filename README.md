# 飞书群消息整理工具

**当前版本**: v1.7.0

自动读取飞书群聊消息，提取网络链接，解析文章信息，写入本地 CSV 和飞书多维表格，处理后自动撤回群聊消息。

> **📢 重要提示**：项目目录结构已重新组织（2026-02-11）
> - 所有Python源代码已移至 `src/` 目录
> - 运行命令需要添加目录前缀，如：`python src/goMessage.py`
> - 详见：[项目结构说明](docs/PROJECT_STRUCTURE.md)

## 功能特性

- ✅ 自动读取指定飞书群聊的消息
- ✅ 提取消息中的网络链接
- ✅ 解析文章标题、发布日期、来源等信息
- ✅ 支持微信公众号、小红书、微博、知乎等平台
- ✅ 写入本地 CSV 文件
- ✅ 非重复数据自动写入飞书多维表格（bitable）
- ✅ URL 重复检测（CSV + 多维表格双重去重）
- ✅ Pin 消息自动标记
- ✅ 重复记录和解析异常自动记录到事件日志
- ✅ 处理成功后自动撤回群聊消息
- ✅ 记录处理进度，增量更新
- ✅ Token 自动刷新机制
- ✅ 灵活的命令行参数控制
- ✅ 自动分类：利用 LLM 对多维表格记录自动打标签/分类

## 环境要求

- Python 3.7+
- 飞书开放平台应用（已创建）

## 安装步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置飞书应用

#### 2.1 在飞书开放平台配置权限

登录 [飞书开放平台](https://open.feishu.cn/)，为你的应用添加以下权限：

**必需权限（用户身份）：**
- `im:chat:readonly` - 读取群聊信息
- `im:message:readonly` - 读取消息内容
- `im:message` - 获取与发送消息
- `bitable:app` - 读写多维表格
- `bitable:app:readonly` - 读取多维表格

**必需权限（应用身份）：**
- `im:message` - 获取与发送消息
- `im:message.group_msg` - 获取群组消息
- `im:message:readonly` - 读取消息内容（用于获取 Pin 消息列表）

#### 2.2 配置重定向 URL

在飞书开放平台 → 应用详情 → 安全设置 → 重定向 URL 中添加：

```
http://localhost:8080/callback
```

#### 2.3 将机器人添加到群聊

在飞书中将你的应用机器人添加到目标群聊中。

### 3. 配置 config.yaml

打开 `config.yaml` 文件，填写以下信息：

```yaml
feishu:
  app_id: "YOUR_APP_ID"          # 替换为你的 App ID
  app_secret: "YOUR_APP_SECRET"  # 替换为你的 App Secret
  redirect_uri: "http://localhost:8080/callback"

target_chat:
  name: "from_微信WeChat"  # 目标群聊名称

target_bitable:
  url: "https://xxx.feishu.cn/wiki/xxx?table=xxx"  # 多维表格 URL
  wiki_token: "xxx"          # 从 URL 中提取
  app_token: ""              # 首次运行自动填充
  table_id: "xxx"            # 从 URL 中提取
```

### 4. 运行授权脚本

首次使用需要进行授权：

```bash
python src/auth.py
```

脚本会自动打开浏览器，完成授权后会自动保存 Token 到 `config.yaml`。

### 5. 运行主程序

```bash
python src/goMessage.py
```

## 使用说明

### 命令行参数

程序支持以下命令行参数，提供灵活的运行模式：

#### 默认模式（只处理新消息）
```bash
python src/goMessage.py
```
- 只处理上次运行后的新消息
- 自动更新处理时间记录
- **推荐日常使用**

#### 处理所有历史消息
```bash
python src/goMessage.py --all
```
- 处理所有历史消息
- **更新**处理时间记录
- **适合需要重新扫描全部消息的场景**

#### 重置并处理所有消息
```bash
python src/goMessage.py --reset
```
- 重置处理时间记录为 0
- 处理所有历史消息
- **更新**处理时间记录
- 下次运行将从本次处理的最后时间开始
- **适合重新开始整理**
- 注意：不会删除表格中的现有数据，只会追加新数据

#### 处理指定范围的消息
```bash
python src/goMessage.py --start 3 --end 10
```
- 只处理第 3 到第 10 条链接
- 索引从 1 开始计数
- 可以单独使用 --start 或 --end
- **适合测试和调试特定消息**

#### 显示未含链接的消息
```bash
python src/goMessage.py --list-nolink
```
- 显示未含链接的消息详细清单
- 包括消息序号、时间和内容
- **此模式下只列出消息，不进行后续的链接解析和存储工作**
- 会显示消息解析错误信息（如果有）
- 默认情况下只显示统计数量
- **适合查看哪些消息没有链接，或排查消息解析问题**

#### 查看帮助
```bash
python src/goMessage.py --help
```

### 首次运行

首次运行时，程序会：
1. 自动查找目标群聊
2. 获取多维表格信息（通过 wiki API 获取 app_token）
3. 处理所有历史消息中的链接
4. 写入本地 CSV 和飞书多维表格
5. 自动撤回已处理的群聊消息

### 后续运行

后续运行时，程序只会处理上次运行后的新消息，避免重复处理。

### Token 过期处理

当 Token 过期时，程序会尝试自动刷新。如果刷新失败，会提示重新运行授权脚本：

```bash
python src/auth.py
```

## 数据存储说明

### 本地 CSV 文件

程序会将所有提取的信息（包括重复记录）写入本地 CSV 文件，字段包括：

| 字段名 | 说明 | 示例 |
|--------|------|------|
| 消息序号 | 消息在本次处理中的序号（从1开始） | "5" |
| 文章标题 | 文章的标题 | "如何使用飞书 API" |
| 文章发表日期 | 发布日期（YYYYMMDD 格式） | "20240209" |
| 星期 | 发布日期对应的星期 | "周五" |
| 文章链接URL | 文章的完整链接 | "https://..." |
| 摘录异常信息 | 解析过程中的错误信息 | "未提取日期" |
| 文章来源 | 文章来源平台 | "微信-公众号名称" |
| 备注 | 访问异常、非微信网址等备注信息 | "非微信网址" |
| 是否重复 | 与已有记录URL相同时标记 | "重复" |
| 标记 | Pin 消息标记 | "Pin" |

### 飞书多维表格

非重复数据按发表日期排序后写入飞书多维表格，字段：

| 字段名 | 说明 |
|--------|------|
| 标题 | 文章标题 |
| 日期 | YYYYMMDD 格式 |
| 星期 | 对应星期 |
| 链接 | 完整链接 |
| 来源 | 来源平台 |

### 事件日志

| 日志文件 | 用途 |
|----------|------|
| `log-err/duplicate_log.csv` | 记录重复数据（标题、日期、链接、来源、消息序号、记录时间） |
| `log-err/parse_error_log.csv` | 记录解析异常数据（链接、来源、异常信息、消息序号、记录时间） |
| `log-err/bitable_fail_log.csv` | 记录多维表格入库失败数据（标题、日期、链接、来源、失败原因、消息序号、记录时间） |
| `log-err/weekly_parse_error_log.csv` | 周报处理解析异常日志 |
| `log-err/wta_errors_{date}.csv` | WaytoAGI 知识库处理解析异常日志 |

**消息序号说明：**
- 序号从 1 开始，按消息时间顺序编号
- 同一条消息中的多个链接会有相同的消息序号
- 方便追溯链接来自哪条群聊消息

**备注字段说明：**
- 微信公众号文章：备注为空
- 非微信网址：自动标识"非微信网址"
- 访问异常：记录具体的异常信息

### 来源识别规则

程序基于 URL 特征自动识别文章来源，目前支持以下网站：

| 来源名称 | URL 特征 | URL截断 |
|---------|---------|---------|
| 微信-{公众号名称} | mp.weixin.qq.com | ✓ 保留"?"之前 |
| APP-华尔街见闻 | wallstreetcn.com/articles/ | - |
| APP-哔哩哔哩 | b23.tv/ | - |
| APP-知乎 | zhuanlan.zhihu.com/p/ 或 www.zhihu.com/question/ | ✓ 保留"?"之前（question） |
| Get笔记OT分享 | www.biji.com/note/share_note/ | - |
| App-小红书 | www.xiaohongshu.com/discovery/item/ 或 xhslink.com | - |
| App-微博 | m.weibo.cn/status/ 或 weibo.com | ✓ 保留"?"之前 |
| APP-即刻 | m.okjike.com/originalPosts/ | - |
| 飞书OT分享 | *.feishu.cn/wiki/ 或 *.feishu.cn/docx/ | ✓ 保留"?"之前 |
| 头条_video | m.toutiao.com/video/ 或 www.toutiao.com/video/ | ✓ 保留"?"之前 |
| 头条OT | m.toutiao.com/article/ 或 www.toutiao.com/article/ | ✓ 保留"?"之前 |
| 小宇宙OT | www.xiaoyuzhoufm.com/episode/ | ✓ 保留"?"之前 |
| APP-同花顺 | .10jqka.com.cn/m/post | ✓ 保留"?pid=xxx" |
| Web-观猹 | watcha.cn/products/ | - |

**URL截断说明：**
- 对于标记为"✓"的来源，程序会自动截断URL，只保留第一个"?"字符之前的内容
- 这样可以去除追踪参数，保持URL简洁一致
- 示例：`https://m.weibo.cn/status/123?from=timeline` → `https://m.weibo.cn/status/123`

**架构说明：**
- 程序采用规则驱动的架构，基于 URL 特征识别网站来源
- 每个网站对应特定的解析方法（标题、日期提取逻辑）
- 支持轻松扩展到更多网站，只需在 `src/url_parser.py` 的 `site_rules` 中添加新规则

**添加新网站支持：**
1. 在 `src/url_parser.py` 的 `__init__` 方法中的 `site_rules` 列表添加新规则
2. 格式：`('URL特征', ('来源名称', 解析方法, 是否截断URL))`
3. 如果需要特殊解析逻辑，创建新的 `_parse_xxx_article` 方法
4. 否则使用通用的 `_parse_general_article` 方法

**解析能力说明：**
- **飞书OT分享**：✓ 可以提取标题和日期（支持 wiki 和 docx 链接）
- **头条_video**：✓ 可以提取标题和日期（支持PC端和移动端URL）
- **头条OT**：✓ 可以提取标题和日期（支持PC端和移动端URL）
- **App-微博**：⚠️ 需要登录，通常只能识别来源，无法提取标题和日期
- **小宇宙OT**：✓ 可以提取标题和日期
- **APP-同花顺**：✓ 可以提取标题和日期（通过API解析）
- **APP-知乎（问答）**：⚠️ 受 zse-ck 反爬限制，仅能识别来源和截断 URL，暂无法提取标题和日期
- **Web-观猹**：⚠️ 页面为 JS 渲染，仅能识别来源，暂无法提取标题和日期
- 详细说明请查看：[docs/technical/URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md](docs/technical/URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md)

### 异常信息说明

当无法提取某些信息时，会在"摘录异常信息"列记录：
- `未提取来源` - 无法识别文章来源
- `未提取日期` - 无法提取发布日期
- `未提取标题` - 无法提取文章标题
- `需要登录或无法访问` - 网站需要登录才能访问

## 文件说明

详细的目录结构说明请查看：[docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)

- **VERSION** - 版本号文件
- **docs/CHANGELOG.md** - 变更日志
- **docs/prd/PRD-001.md** - 产品需求文档 v1.0.0
- **docs/prd/PRD-002.md** - 产品需求文档 v1.1.0
- **docs/PRD_REGISTRY.md** - PRD 总集台账

```
├── config.yaml.template         # 配置文件模板
├── requirements.txt           # Python 依赖包
├── README.md                  # 本文档
├── QUICKSTART.md              # 快速开始指南
├── VERSION                    # 版本号文件
├── _todo.md                   # 待办事项
│
├── cfg/                       # 配置文件目录
│   ├── config.yaml            # 业务配置（不提交到版本控制）
│   ├── credentials.yaml       # 敏感凭证（不提交到版本控制）
│   └── wxgzh_list.yaml        # 微信公众号清单配置
│
├── src/                       # 源代码目录
│   ├── goMessage.py           # 群消息主程序
│   ├── goAIPM.py              # 周报处理主程序
│   ├── goWTA.py               # WaytoAGI 知识库处理主程序
│   ├── goWXGZH.py               # 微信公众号历史文章处理主程序
│   ├── autoClassify.py          # 自动分类主程序（LLM 驱动）
│   ├── auth.py                  # 授权脚本
│   ├── feishu_client.py       # 飞书 API 客户端
│   ├── url_parser.py          # URL 解析器
│   ├── modules/               # 公共模块
│   │   ├── bitable_url_cache.py   # 多维表格 URL 本地缓存
│   │   ├── config_utils.py        # 配置文件工具函数
│   │   └── feishu_auth.py         # 飞书授权模块
│   ├── tools_excel/           # Excel 工具脚本
│   └── archive/               # 备存脚本
│       ├── recall_messages.py     # 手动撤回消息脚本
│       └── import_feishu_to_csv.py # 飞书电子表格导入脚本
│
├── tests/                     # 测试脚本目录
│   ├── test_parser.py         # URL 识别测试脚本
│   ├── test_toutiao.py        # 头条解析测试
│   └── test_all_improvements.py  # 综合测试
│
├── docs/                      # 文档目录
│   ├── CHANGELOG.md           # 变更日志
│   ├── PRD_REGISTRY.md        # PRD 总集台账
│   ├── prd/                   # PRD 文档目录
│   ├── guides/                # 使用指南
│   ├── technical/             # 技术文档
│   └── archive/               # 历史文档归档
│
├── temp/                      # 临时文件目录
├── data/                      # 数据目录
│   └── *.csv                  # 本地 CSV 数据文件
├── log-err/                   # 错误日志目录
│   ├── duplicate_log.csv      # 重复记录日志
│   ├── parse_error_log.csv    # 解析异常日志
│   ├── bitable_fail_log.csv   # 多维表格入库失败日志
│   ├── weekly_parse_error_log.csv # 周报处理异常日志
│   ├── wxgzh_error_log.csv    # 微信公众号处理异常日志
│   └── wta_errors_*.csv       # WTA 处理异常日志
└── 历史文档/                  # 中文历史文档
```

## 撤回消息功能

⚠️ **重要说明**：飞书API只支持"撤回消息"功能，撤回后群里会显示"XXX撤回了一条消息"的提示。
无法实现客户端的"删除"功能（仅自己隐藏消息）。详见：[docs/technical/FEISHU_MESSAGE_DELETE_NOTE.md](docs/technical/FEISHU_MESSAGE_DELETE_NOTE.md)

### 自动撤回

主程序处理完成后会自动撤回已处理的群聊消息：
- 有非重复数据时：等待多维表格写入成功后才撤回
- 全部重复时：直接撤回
- 多维表格写入失败时：不撤回（保护数据安全）

### 手动撤回

如果需要手动撤回群聊中的特定消息，可以使用 `src/archive/recall_messages.py` 脚本。

### 快速使用

```bash
# 列出所有消息及其序号
python src/archive/recall_messages.py --list

# 试运行，查看将要撤回的消息
python src/archive/recall_messages.py --indices 1,3,5 --dry-run

# 实际撤回
python src/archive/recall_messages.py --indices 1,3,5

# 逐条确认撤回（推荐）
python src/archive/recall_messages.py --indices 1,3,5 --confirm-each
```

### 支持的序号格式

- 单个序号：`--indices 1`
- 多个序号：`--indices 1,3,5`
- 范围：`--indices 1-5`
- 混合：`--indices 1,3-5,7,10-15`

### 安全特性

- ✅ **倒序撤回**：自动从编号最大的消息开始撤回，避免序号混乱
- ✅ **逐条确认**：使用 `--confirm-each` 可以在撤回每条消息前单独确认（y=撤回, n=跳过, a=确认剩余全部, q=退出）
- ✅ **试运行模式**：使用 `--dry-run` 可以先查看将要撤回的消息
- ⚠️ **撤回提示**：撤回后群里会显示"XXX撤回了一条消息"的提示

### 权限要求

⚠️ **重要：** 撤回消息需要特殊权限：
- 授权用户必须是目标群聊的**管理员**或**群主**
- 或者应用机器人必须是群管理员
- 只能撤回自己发送的消息，或作为管理员撤回群内消息

详细说明请查看：[docs/guides/RECALL_MESSAGES_GUIDE.md](docs/guides/RECALL_MESSAGES_GUIDE.md)

## 注意事项

1. **配置文件安全**: `cfg/credentials.yaml` 包含敏感信息，请勿提交到版本控制系统
2. **权限申请**: 确保在飞书开放平台申请了所有必需的权限
3. **网络访问**: 部分网站（如小红书、微博）可能需要登录才能访问，会在备注中记录
4. **请求频率**: 程序会自动控制请求频率，避免触发限流

## 常见问题

### Q: Token 刷新失败怎么办？

A: 重新运行授权脚本：`python src/auth.py`

### Q: 找不到目标群聊？

A: 检查 `config.yaml` 中的群聊名称是否正确，确保与飞书中的群名完全一致。

### Q: 无法访问某些网站？

A: 部分网站需要登录或有反爬虫机制，程序会在"备注"字段中记录访问异常。

### Q: 如何定时运行？

A: 可以使用系统的定时任务功能：
- **Windows**: 任务计划程序
- **Linux/Mac**: crontab

示例 crontab 配置（每小时运行一次）：
```bash
0 * * * * cd /path/to/project && python src/goMessage.py
```

## 技术支持

如有问题，请检查：
1. Python 版本是否 >= 3.7
2. 依赖包是否正确安装
3. 飞书应用权限是否配置完整
4. Token 是否有效

## 许可证

MIT License
