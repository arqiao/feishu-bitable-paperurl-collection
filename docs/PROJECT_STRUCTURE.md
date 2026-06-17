# 项目结构说明

本文档说明项目的目录结构、文件组织方式以及目录重组的相关信息。

**更新日期**: 2026-04-22

---

## 目录

- [当前目录结构](#当前目录结构)
- [目录说明](#目录说明)
- [命令使用](#命令使用)
- [目录重组说明](#目录重组说明)
- [文件迁移对照表](#文件迁移对照表)

---

## 当前目录结构

```
飞书群消息整理工具/
├── README.md                    # 项目主文档
├── QUICKSTART.md                # 快速开始指南
├── VERSION                      # 版本号文件（当前 1.8.0）
├── requirements.txt             # Python依赖
├── config.yaml.template         # 配置文件模板
├── _todo.md                     # 待办事项
├── _todo_draft.md               # 思路草稿
│
├── cfg/                         # 配置文件目录
│   ├── config.yaml              # 业务配置（不提交到版本控制）
│   ├── credentials.yaml         # 敏感凭证（不提交到版本控制）
│   ├── config.yaml.template     # 配置文件模板
│   └── wxgzh_list.yaml          # 微信公众号清单配置
│
├── src/                         # 源代码目录
│   ├── README.md                # 源代码目录说明
│   ├── goMessage.py             # 群消息主程序
│   ├── goAIPM.py                # 周报处理主程序
│   ├── goWTA.py                 # WaytoAGI 知识库处理主程序
│   ├── goWXGZH.py               # 微信公众号历史文章处理主程序
│   ├── autoClassify.py          # 自动分类主程序（LLM 驱动）
│   ├── auth.py                  # 授权脚本
│   ├── feishu_client.py         # 飞书API客户端
│   ├── url_parser.py            # URL解析器（原 article_parser.py）
│   ├── recall_messages.py       # 消息撤回脚本
│   │
│   ├── modules/                 # 公共模块目录
│   │   ├── bitable_url_cache.py # 多维表格 URL 本地缓存
│   │   ├── config_utils.py      # 配置文件工具函数
│   │   └── feishu_auth.py       # 飞书授权模块
│   │
│   └── tools_excel/             # Excel 工具脚本目录
│       ├── complete_excel_repair.py
│       ├── repair_excel.py
│       ├── remove_empty_rows.py
│       ├── remove_empty_rows_pandas.py
│       ├── simple_remove_empty_rows.py
│       ├── simple_excel_cleaner.py
│       └── final_fix.py
│
├── tests/                       # 测试脚本目录
│   ├── README.md                # 测试目录说明
│   ├── test_parser.py           # URL识别测试
│   ├── test_toutiao.py          # 头条解析测试
│   └── test_all_improvements.py # 综合测试
│
├── docs/                        # 文档目录
│   ├── README.md                # 文档目录说明
│   ├── CHANGELOG.md             # 变更日志
│   ├── PRD_REGISTRY.md          # PRD 总集台账
│   ├── PROJECT_STRUCTURE.md     # 项目结构说明
│   │
│   ├── prd/                     # PRD 文档目录
│   │   └── PRD-001.md           # 产品需求文档 v1.0.0
│   │
│   ├── guides/                  # 使用指南（面向用户）
│   │   ├── RECALL_MESSAGES_GUIDE.md  # 消息撤回功能指南
│   │   ├── MESSAGE_INDEX_GUIDE.md    # 消息序号说明
│   │   └── MESSAGE_DELETION_FAQ.md   # 常见问题
│   │
│   ├── technical/               # 技术文档（面向开发者）
│   │   ├── ARCHITECTURE.md      # 架构设计
│   │   ├── CSV_LOG_SPEC.md      # CSV 日志和缓存文件规范
│   │   ├── TOKEN_MANAGEMENT.md  # Token管理
│   │   ├── FEISHU_ACCESS_NOTE.md # 飞书访问说明
│   │   ├── FEISHU_MESSAGE_DELETE_NOTE.md # 消息管理说明
│   │   ├── WEIBO_PARSING_ISSUE.md # 微博解析问题
│   │   └── URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md # URL处理改进
│   │
│   └── archive/                 # 历史文档归档
│       ├── RECALL_SAFETY_FEATURES.md
│       ├── DOCUMENTATION_UPDATE.md
│       ├── IMPROVEMENTS_SUMMARY.md
│       ├── FINAL_IMPROVEMENTS_SUMMARY.md
│       └── TOUTIAO_ARTICLE_SUMMARY.md
│
├── temp/                        # 临时文件目录
│   ├── temp_wechat.html
│   ├── patch_missing_urls.py    # 补丁：扫描遗漏的纯文本二级 URL
│   ├── patch_wta1o.py           # 补丁：WTA 数据修补
│   └── *.png
│
├── log-err/                     # 错误日志及处理输出目录
│   ├── duplicate_log.csv        # 重复记录日志
│   ├── parse_error_log.csv      # 解析异常日志
│   ├── bitable_fail_log.csv     # 多维表格入库失败日志
│   ├── weekly_parse_error_log.csv # 周报处理异常日志
│   ├── wxgzh_error_log.csv      # 微信公众号处理异常日志
│   ├── wta_errors_*.csv         # WTA 处理异常日志
│   ├── wta_urls_*.csv           # WTA 提取的 URL 列表
│   └── wta_parsed_*.csv         # WTA 解析结果
│
├── data/                        # 数据目录
│   ├── message_log.csv          # 群消息主日志
│   ├── wta_processed_urls.csv   # WTA 本地去重缓存（utf-8-sig）
│   └── bitable_cache_*.csv      # 多维表格 URL 本地缓存（按 table_id）
│
└── 历史文档/                    # 中文历史文档
    ├── 历史修改指令.txt
    ├── 网站识别规则.txt
    └── 需求草案.txt
```

---

## 目录说明

### 根目录
保留最重要的文档和配置文件：
- **README.md** - 项目主文档，包含完整的使用说明
- **QUICKSTART.md** - 快速开始指南
- **VERSION** - 版本号文件（纯文本，当前 1.7.0）
- **requirements.txt** - Python依赖包列表
- **config.yaml.template** - 配置文件模板
- **_todo.md** - 待办事项和已知限制

### cfg/ - 配置文件
所有配置文件集中管理（2个业务文件 + 1个公众号清单）：
- **config.yaml** - 业务配置文件（profiles、bitable_columns 等，不提交到版本控制）
- **credentials.yaml** - 敏感凭证文件（token、secret、cookie 等，不提交到版本控制）
- **wxgzh_list.yaml** - 微信公众号清单（fakeid、名称、更新频率、last_update）

### src/ - 源代码
所有Python源代码文件（8个）+ 公共模块 + 工具脚本：
- **goMessage.py** - 群消息主程序，负责提取消息、解析链接、写入表格
- **goAIPM.py** - 周报处理主程序，从飞书 wiki 文档提取 URL → 解析 → 日报交叉检查 → 写入多维表格；支持 --file/--list/--daily/--update/--weekly 五种模式
- **goWTA.py** - WaytoAGI 知识库处理主程序，从知识库文档提取 URL → 两层解析 → 父记录关联 → 写入多维表格
- **goWXGZH.py** - 微信公众号历史文章处理主程序，基于公众号清单抓取文章 → 解析 → 写入多维表格
- **autoClassify.py** - 自动分类主程序，利用 LLM 对多维表格中的记录自动分类打标签
- **auth.py** - 授权脚本，获取飞书API访问令牌
- **feishu_client.py** - 飞书API客户端，封装所有API交互
- **url_parser.py** - URL解析器（原 article_parser.py），从各网站提取文章信息
- **recall_messages.py** - 消息撤回脚本，撤回群聊中的特定消息

### src/modules/ - 公共模块
从各主程序中抽取的共享模块（3个）：
- **bitable_url_cache.py** - 多维表格 URL 本地缓存，按 table_id 分文件存储，避免全量拉取
- **config_utils.py** - 配置文件工具函数，文本替换方式更新字段，保留注释和格式
- **feishu_auth.py** - 飞书授权脚本模块化

### src/tools_excel/ - Excel 工具脚本
Excel 文件修复和清理工具（7个）：
- 空行清理、文件修复、数据清洗等一次性工具脚本

### tests/ - 测试
所有测试脚本（3个）：
- **test_parser.py** - URL识别规则测试
- **test_toutiao.py** - 头条文章解析测试
- **test_all_improvements.py** - 综合功能测试

### docs/ - 文档
文档按类型分为三个子目录：

#### docs/guides/ - 使用指南
面向用户的使用说明文档（3个）：
- **RECALL_MESSAGES_GUIDE.md** - 消息撤回功能详细使用指南
- **MESSAGE_INDEX_GUIDE.md** - 消息序号功能说明
- **MESSAGE_DELETION_FAQ.md** - 常见问题解答

#### docs/technical/ - 技术文档
面向开发者的技术说明文档（7个）：
- **ARCHITECTURE.md** - 项目架构设计说明
- **CSV_LOG_SPEC.md** - CSV 日志和缓存文件规范
- **TOKEN_MANAGEMENT.md** - Token管理机制说明
- **FEISHU_ACCESS_NOTE.md** - 飞书Wiki访问限制说明
- **FEISHU_MESSAGE_DELETE_NOTE.md** - 飞书消息管理机制详解
- **WEIBO_PARSING_ISSUE.md** - 微博解析问题分析
- **URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md** - URL处理改进说明

#### docs/archive/ - 历史归档
不再活跃但有参考价值的历史文档（5个）

### temp/ - 临时文件
临时生成的文件，已添加到 `.gitignore`，不提交到版本控制

### 历史文档/ - 中文历史文档
早期的中文文档和需求记录（3个）

---

## 命令使用

### 运行主程序
```bash
python src/goMessage.py              # 处理新消息
python src/goMessage.py --all        # 处理所有历史消息
python src/goMessage.py --reset      # 重置并处理所有消息
python src/goMessage.py --profile ai # 指定 profile 运行
```

### 运行周报处理
```bash
python src/goAIPM.py --file <url>             # 处理单个周报文档
python src/goAIPM.py --list <listfile>        # 批量处理周报文档列表
python src/goAIPM.py --daily <url>            # 处理单个日报文档
python src/goAIPM.py --update                 # 自动处理新日报（基于 last_processed_date）
python src/goAIPM.py --weekly <url>           # 基于周报 wiki 完善多维表格
```

### 运行 WaytoAGI 知识库处理
```bash
python src/goWTA.py --his 20260301 20260307  # 历史批量处理
python src/goWTA.py --update                  # 增量更新
```

### 运行微信公众号处理
```bash
python src/goWXGZH.py --his 20260322 20260331  # 历史批量处理
python src/goWXGZH.py --update                  # 增量更新
python src/goWXGZH.py --searchbiz "DeepTech深科技"  # 搜索公众号
python src/goWXGZH.py --refresh-cache           # 强制重建 URL 缓存
```

### 运行自动分类
```bash
python src/autoClassify.py                      # 对目标表中未分类记录自动打标签
python src/autoClassify.py --dry-run             # 试运行，仅预览分类结果不写入
python src/autoClassify.py --batch-size 20       # 指定每批处理数量
```

### 运行授权
```bash
python src/auth.py              # 获取访问令牌
```

### 撤回消息
```bash
python src/archive/recall_messages.py --list              # 列出所有消息
python src/archive/recall_messages.py --indices 1,3,5    # 撤回指定消息
python src/archive/recall_messages.py --indices 1-5 --dry-run  # 试运行
```

### 运行测试
```bash
python tests/test_parser.py     # 测试URL识别
python tests/test_toutiao.py    # 测试头条解析
```

---

## 目录重组说明

### 重组时间
2026-02-11

### 重组原因
为了更好地组织项目文件，提高可维护性和可扩展性，我们将文件重新分类到不同的目录中。

### 主要改进

1. **代码组织**
   - ✅ 所有Python源代码集中到 `src/` 目录
   - ✅ 测试脚本独立到 `tests/` 目录
   - ✅ 便于代码管理和版本控制

2. **文档分类**
   - ✅ 使用指南 (`docs/guides/`) - 面向用户
   - ✅ 技术文档 (`docs/technical/`) - 面向开发者
   - ✅ 历史归档 (`docs/archive/`) - 保留参考价值

3. **临时文件管理**
   - ✅ 临时文件集中到 `temp/` 目录
   - ✅ 已添加到 `.gitignore`，不提交到版本控制

4. **历史文档保留**
   - ✅ 中文历史文档保留在 `历史文档/` 目录
   - ✅ 保持原有的需求和修改记录

### 使用影响

**命令变化**：所有Python脚本的运行命令都需要添加目录前缀

| 功能 | 旧命令 | 新命令 |
|------|--------|--------|
| 主程序 | `python goMessage.py` | `python src/goMessage.py` |
| 授权 | `python auth.py` | `python src/auth.py` |
| 撤回消息 | `python recall_messages.py --list` | `python src/archive/recall_messages.py --list` |
| 测试 | `python test_parser.py` | `python tests/test_parser.py` |

**不受影响的部分**：
- ✅ 配置文件位置不变 (`config.yaml`)
- ✅ 依赖文件位置不变 (`requirements.txt`)
- ✅ 主文档位置不变 (`README.md`)
- ✅ 所有功能保持不变

---

## 文件迁移对照表

### Python源代码文件

| 旧位置 | 新位置 |
|--------|--------|
| `goMessage.py` | `src/goMessage.py` |
| `auth.py` | `src/auth.py` |
| `feishu_client.py` | `src/feishu_client.py` |
| `article_parser.py` | `src/article_parser.py` |
| `recall_messages.py` | `src/archive/recall_messages.py` |

### 测试脚本

| 旧位置 | 新位置 |
|--------|--------|
| `test_parser.py` | `tests/test_parser.py` |
| `test_toutiao.py` | `tests/test_toutiao.py` |
| `test_all_improvements.py` | `tests/test_all_improvements.py` |

### 使用指南文档

| 旧位置 | 新位置 |
|--------|--------|
| `RECALL_MESSAGES_GUIDE.md` | `docs/guides/RECALL_MESSAGES_GUIDE.md` |
| `MESSAGE_INDEX_GUIDE.md` | `docs/guides/MESSAGE_INDEX_GUIDE.md` |
| `MESSAGE_DELETION_FAQ.md` | `docs/guides/MESSAGE_DELETION_FAQ.md` |

### 技术文档

| 旧位置 | 新位置 |
|--------|--------|
| `ARCHITECTURE.md` | `docs/technical/ARCHITECTURE.md` |
| `TOKEN_MANAGEMENT.md` | `docs/technical/TOKEN_MANAGEMENT.md` |
| `FEISHU_ACCESS_NOTE.md` | `docs/technical/FEISHU_ACCESS_NOTE.md` |
| `FEISHU_MESSAGE_DELETE_NOTE.md` | `docs/technical/FEISHU_MESSAGE_DELETE_NOTE.md` |
| `WEIBO_PARSING_ISSUE.md` | `docs/technical/WEIBO_PARSING_ISSUE.md` |
| `URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md` | `docs/technical/URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md` |

### 历史归档文档

| 旧位置 | 新位置 |
|--------|--------|
| `RECALL_SAFETY_FEATURES.md` | `docs/archive/RECALL_SAFETY_FEATURES.md` |
| `DOCUMENTATION_UPDATE.md` | `docs/archive/DOCUMENTATION_UPDATE.md` |
| `IMPROVEMENTS_SUMMARY.md` | `docs/archive/IMPROVEMENTS_SUMMARY.md` |
| `FINAL_IMPROVEMENTS_SUMMARY.md` | `docs/archive/FINAL_IMPROVEMENTS_SUMMARY.md` |
| `TOUTIAO_ARTICLE_SUMMARY.md` | `docs/archive/TOUTIAO_ARTICLE_SUMMARY.md` |

### 临时文件

| 旧位置 | 新位置 |
|--------|--------|
| `temp_wechat.html` | `temp/temp_wechat.html` |
| `20260211-023544.png` | `temp/20260211-023544.png` |

### 中文历史文档

| 旧位置 | 新位置 |
|--------|--------|
| `历史修改指令.txt` | `历史文档/历史修改指令.txt` |
| `网站识别规则.txt` | `历史文档/网站识别规则.txt` |
| `需求草案.txt` | `历史文档/需求草案.txt` |

---

## 后续维护建议

### 添加新功能
- 源代码放入 `src/`
- 测试脚本放入 `tests/`
- 使用文档放入 `docs/guides/`
- 技术文档放入 `docs/technical/`

### 文档管理
- 保持文档与代码同步
- 过时文档移至 `docs/archive/`
- 在各目录的 README.md 中更新说明

### 临时文件
- 所有临时文件放入 `temp/`
- 定期清理不需要的临时文件

---

## 常见问题

### Q: 为什么要重组目录？

A: 新的目录结构带来以下好处：
1. **更清晰的组织** - 代码、测试、文档分离
2. **更易维护** - 相关文件集中管理
3. **更好的可扩展性** - 便于添加新功能和文档
4. **符合最佳实践** - 遵循Python项目标准结构

### Q: 如果遇到"找不到文件"的错误怎么办？

A:
1. 检查是否使用了旧的文件路径
2. 参考本文档的命令使用部分更新路径
3. 确保从项目根目录运行命令

### Q: 配置文件位置变了吗？

A: 没有。`config.yaml`、`requirements.txt`、`README.md` 等核心文件仍在项目根目录。

---

## 文件统计

- **源代码**: 9个Python文件 + 3个公共模块 + 7个Excel工具脚本
- **配置文件**: 3个（config.yaml、credentials.yaml、wxgzh_list.yaml）在 cfg/ 目录
- **测试脚本**: 3个Python文件
- **使用指南**: 3个Markdown文件
- **技术文档**: 7个Markdown文件
- **历史归档**: 5个Markdown文件
- **中文文档**: 3个文本文件
- **临时文件**: 若干
- **版本管理**: 2个文件（VERSION 在根目录、CHANGELOG.md 在 docs/）
- **产品文档**: 1个文件（PRD-001.md）+ 1个台账（PRD_REGISTRY.md）

**总计**: 31个主要文件，分类到9个目录中

---

## 参考文档

- [README.md](../README.md) - 项目主文档
- [QUICKSTART.md](../QUICKSTART.md) - 快速开始指南
- [docs/README.md](README.md) - 文档目录说明
- [src/README.md](../src/README.md) - 源代码目录说明
- [tests/README.md](../tests/README.md) - 测试目录说明
