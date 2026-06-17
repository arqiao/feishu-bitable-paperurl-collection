# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

---

## [1.8.0] - 2026-04-20 ~ 2026-04-22

### 新增

#### goAIPM.py 新增命令行参数
- `--daily <url>`：处理单个日报文档，从知识星球日报提取 URL → 解析 → 写入多维表格
- `--update`：自动查找 `last_processed_date` 之后的新日报，逐篇处理并更新配置
- `--weekly <url>`：基于周报 wiki 完善多维表格，对已有记录更新精选合集字段，对新 URL 解析后新增

#### goAIPM.py --weekly 模式六阶段流程
1. 提取周报 URL（`extract_urls_from_doc`）
2. 搜索多维表格已有记录（按周报时间过滤）
3. 分类对比（已有 vs 新增，重要 vs 非重要）
4. 解析新增 URL（`parse_urls_phase2`）
5. 批量更新已有记录（`batch_update_bitable_records`）
6. 批量新增记录（`batch_add_bitable_records`）

#### goAIPM.py --update 模式
- `find_daily_articles_since()`：从知识星球群组翻页查找指定日期后的日报
- 按日期去重，避免同一天重复处理
- 处理完成后自动更新 `last_processed_date`
- 当日已处理时提前退出

#### goAIPM.py 精选合集完整层级体系
- `PA-日周++`：重要 URL，同时出现在周报和日报
- `PA-周++`：重要 URL，仅出现在周报
- `PA-日++`：重要 URL，仅出现在日报
- `PR_引用`：引用类 URL
- `PB-日周`：普通 URL，同时出现在周报和日报
- `PB-周`：普通 URL，仅出现在周报
- `PB-日`：普通 URL，仅出现在日报

#### 新增网站解析规则（url_parser.py）
- `garden.zsxq.com/` → `星球-AI产品经理大本营`：专用解析器，强制 UTF-8 编码，确保尾部斜杠
- `xiaobot.net/` → `APP-小报童`：API 解析（Bearer token + MD5 签名认证），支持 `/post/{uuid}` 和 `/p/{slug}` 两种路径

#### 新增/增强解析方法（url_parser.py）
- `_parse_zsxq_garden()`：知识星球 garden 页面，强制 `resp.encoding = 'utf-8'` 解决乱码
- `_parse_xiaobot_article()`：小报童 API 集成，MD5 签名（`md5("dbbc1dd37360b4084c3a69346e0ce2b2.{timestamp}")`），日期优先从标题提取 8 位数字
- `_parse_zsxq_article()` 增加 5 次指数退避重试
- `_parse_shimo()` 从硬编码映射改为动态 API 调用（`shimo.im/lizard-api/files/{doc_id}`）
- `_parse_jike_article()` 时间前缀正则扩展，支持"天/小时/月/年/分钟"等单位

#### bitable_url_cache 全模式接入
- `--daily`、`--update`、`--weekly` 三个模式均已接入缓存更新
- `process_daily_standalone` 新增 `bitable_cache` 参数
- `process_daily_update` 初始化缓存并传递给每篇日报处理
- `process_weekly` 阶段六新增记录后追加缓存

### 变更

#### 凭证配置
- `cfg/credentials.yaml` 新增 `xiaobot.token` 字段
- goAIPM.py、goWTA.py、goWXGZH.py 的 credentials 字典新增 `xiaobot_token`

#### goAIPM.py 日期计算
- `get_next_saturday_weekly_time()` 新增 `ref_date` 参数，支持基于日报日期计算周报时间（而非运行时日期）

### 修复
- 修复 `process_one_doc` 在 url_items 为空时返回值解包错误（缺少第二个返回值 `cache_entries`）
- 修复 `find_daily_articles_since` 同一天日报重复出现的问题（新增按日期去重）
- 修复 `garden.zsxq.com` 无尾部斜杠时 302 重定向到登录页的问题
- 修复 `garden.zsxq.com` 页面编码乱码（强制 UTF-8）
- 修复即刻文章时间前缀正则只匹配"X月前"不匹配"X天前"等格式

---

## [1.7.0] - 2026-04-15 ~ 2026-04-20

### 新增

#### 微信公众号历史文章处理程序 goWXGZH.py
- 新增 `src/goWXGZH.py`：基于公众号清单，抓取指定日期范围的文章，解析后写入飞书多维表格
- 支持历史批量处理（`--his START END`）和增量更新（`--update`）
- 支持搜索公众号（`--searchbiz KEYWORD`）获取 fakeid
- 支持指定公众号清单文件（`--list`，默认 `cfg/wxgzh_list.yaml`）
- 支持强制重建本地 URL 缓存（`--refresh-cache`）
- 微信后台 API 双通道抓取：appmsg 接口 + publish 接口
- 更新频率控制：根据公众号 `freq` 字段（如 `6h`、`1d`、`3d`）计算截止时间
- 错误日志：`log-err/wxgzh_error_log.csv`

#### 公共模块抽取 src/modules/
- 新增 `modules/bitable_url_cache.py`：飞书多维表格 URL 本地缓存模块，按 table_id 分文件存储在 data/ 目录，避免每次运行全量拉取多维表格记录
- 新增 `modules/config_utils.py`：配置文件工具函数，文本替换方式更新字段，保留注释和格式
- 新增 `modules/feishu_auth.py`：飞书授权脚本模块化，从 auth.py 抽取

#### 配置目录 cfg/
- 新增 `cfg/` 目录，存放配置文件
- `config.yaml` 和 `credentials.yaml` 迁移至 `cfg/` 目录
- `cfg/wxgzh_list.yaml`：微信公众号清单配置（fakeid、名称、更新频率、last_update）

#### Excel 工具脚本 src/tools_excel/
- 新增 `src/tools_excel/` 目录，包含多个 Excel 修复/清理工具脚本
- `complete_excel_repair.py`、`repair_excel.py`：Excel 文件修复
- `remove_empty_rows.py`、`remove_empty_rows_pandas.py`、`simple_remove_empty_rows.py`：空行清理
- `simple_excel_cleaner.py`：Excel 清理
- `final_fix.py`：创建全新 Excel 文件的最终修复方案

#### feishu_client.py 新增方法
- `batch_update_bitable_records()`：批量更新多维表格记录
- `search_bitable_records()`：搜索多维表格记录
- `get_bitable_fields()`：获取多维表格字段列表
- `get_spreadsheet_info()`：获取电子表格信息
- `get_spreadsheet_sheets()`：获取电子表格工作表列表
- `get_spreadsheet_column_values()`：获取电子表格列值
- `get_spreadsheet_values()`：获取电子表格区域值

### 变更

#### 模块化重构
- goMessage.py、goAIPM.py、goWTA.py 统一引入 `modules/bitable_url_cache` 替代原有的全量拉取逻辑
- goMessage.py、goWTA.py 引入 `modules/config_utils` 的 `update_config_field()` 替代直接 YAML 写入，保留配置文件注释
- 授权失败提示统一指向 `python src/modules/feishu_auth.py`

#### 配置文件路径迁移
- `config.yaml` 和 `credentials.yaml` 从项目根目录迁移至 `cfg/` 目录
- `feishu_client.py` 默认配置路径更新为 `cfg/config.yaml` 和 `cfg/credentials.yaml`

#### 补丁程序
- `temp/patch_wta1o.py`：WTA 数据修补脚本

---

## [1.6.0] - 2026-03-09 ~ 2026-04-15

### 新增

#### goWTA.py 缓存子节点去重（--update 模式）
- 新增 `_clean_url_for_dedup()` 统一 URL 清洗函数：飞书去参数 + 微信截断（长链接 `&scene=` 截断、短链接去 `?` 参数）+ normalize
- `load_processed_urls()` 新增返回值 `cache_id_to_norm`（cache_id → URL 反查）和 `norm_to_parent_id`（URL → 父节点 cache_id）
- `parse_wta_urls()` 新增缓存子节点去重：对候选重复 L1，根据 CSV 缓存中的父子关系构建子节点 URL 清单，逐个比对二级 URL，只解析新的
- 如果所有二级 URL 均在缓存中，L1 整体判定为完全重复，跳过写入
- 缓存中 Url-B 为 layer=2 时，取其父节点的所有子节点作为清单，并额外检查父节点 URL
- 缓存中 Url-B 为 layer=1 时，取其自身的所有子节点作为清单

#### goWTA.py 模糊去重（已被缓存子节点去重替代）
- 曾新增 `_check_fuzzy_dedup()` 函数和 `fuzzy_skipped_parents` 机制
- 已在缓存子节点去重方案中移除，由更精确的父子关系匹配替代

#### goWTA.py 预过滤优化（--update 模式）
- L1 URL 通过 `processed_urls` 和 `dedup_keys` 判断已存在/已去重，跳过 `parse_url` 解析
- L2 URL 通过 `dedup_norms`（URL-only 集合）跳过解析
- 飞书 wiki URL 同时检查原始 URL 和 `_clean_feishu_wiki_url` 清洗后的 URL，解决 `?from=from_copylink` 参数导致的去重失败

#### goAIPM.py 知识星球重试增强
- `resolve_zsxq_short_to_article()` 从 3 次固定 2s 延迟改为 5 次指数退避（2s、4s、6s、8s）
- 新增批量补重试阶段：所有日报处理完成后，等待 10s，对失败项统一重试

#### goAIPM.py 输出格式化
- 各阶段标题、完成行、段落之间增加换行符，提升可读性

### 变更

#### feishu_client.py 全局超时
- `_request()` 方法新增 `kwargs.setdefault('timeout', 30)`，所有飞书 API 调用默认 30s 超时
- 解决阶段二/阶段三后程序无响应挂起的问题

#### goAIPM.py 超时保护
- `get_doc_blocks()` 和 `get_doc_title()` 新增 `timeout=30`
- 异常处理增加 `requests.exceptions.Timeout`

#### goWTA.py 去重架构调整
- `write_to_bitable()` 移除 `_check_fuzzy_dedup` 模糊去重和 `parent_children` 参数
- 去重逻辑从阶段三前移到阶段二（`parse_wta_urls`），减少无效 HTTP 请求
- `load_processed_urls()` 返回值从 5-tuple 扩展为 7-tuple
- `init_processed_urls_from_bitable()` 返回值对齐

### 修复
- 修复 goWTA.py 阶段二结束后程序挂起：`get_doc_blocks` 无 timeout 导致 HTTP 请求无限等待
- 修复 goWTA.py 阶段三结束后程序挂起：`feishu_client._request` 无 timeout 导致 API 调用无限等待
- 修复飞书 wiki URL `?from=from_copylink` 参数导致 L1 去重失败：新增 `_clean_feishu_wiki_url` 清洗后的双重检查
- 修复 `parse_wta_urls` 引用未传入的 `url_to_cache_id` 参数导致 NameError
- 修复 L2 URL 使用精确三元组 `(norm, '2', parent_cache_id)` 去重时因 orig 反转父子关系导致匹配失败：改为 URL-only 匹配

---

## [1.5.0] - 2026-03-07 ~ 2026-03-08

### 新增

#### WaytoAGI 知识库处理程序 goWTA.py
- 从 WaytoAGI 飞书知识库文档中按日期范围提取 URL，解析文章信息，写入飞书多维表格
- 支持历史批量处理（`--his START END`）和增量更新（`--update`）
- 两层 URL 提取：第一层从知识库文档提取 wiki 页面，第二层从 wiki 页面提取外部链接
- 父记录关联：L2 URL 自动关联到 L1 URL，带"原文链接"标记的 URL 成为顶层父记录
- 飞书文档日期提取：通过 drive meta API 获取 `latest_modify_time`
- 无效 URL 过滤：提取阶段自动排除 localhost、127.0.0.1、伪 URL（如 auth.py、brand-voice.md）
- `--update` 模式优先扫描近期文档，仅在近期文档无结果时才扫描归档文档
- 纯文本 URL 提取：`_extract_text_links` 同时提取超链接和正文中的纯文本 URL
- 纯文本 URL 截断：自动去除末尾全角右括号"）"
- 重试阶段进度显示：`[重试 1/184]` 格式

#### 新增网站解析规则（url_parser.py）
- `waytoagi.feishu.cn/record/` → `飞书-通往AGI之路`（多维表格记录，shareRecord 解析）
- `waytoagi.feishu.cn/` → `飞书-通往AGI之路`（优先于通用飞书规则）
- `.feishu.cn/record/` → `飞书OT分享`（多维表格记录）
- `.feishu.cn/base/` → `飞书OT分享`（多维表格首页，SERVER_DATA.meta 解析）
- `bytedance.larkoffice.com/` → `飞书-字节跳动`（截断 URL 至 `?` 前）
- `modelscope.cn/` → `Web-魔搭`
- `github.com/` → `Web-GitHub`
- `api-docs.deepseek.com/` → `Web-DeepSeek`
- `ae.feishu.cn/` → `Web-飞书aPaaS`
- `youtu.be/` → `Web-Youtube`（短链接）
- `x.com/` → `Web-X`

#### 新增解析方法（url_parser.py）
- `_parse_feishu_record()`：从页面 `window.SERVER_DATA.shareRecord` JSON 提取标题和日期
- `_parse_feishu_base()`：从页面 `window.SERVER_DATA.meta` 提取标题，`edit_time` 正则提取日期

#### 飞书文档 API 解析增强（url_parser.py）
- 新增 `_parse_feishu_via_api()`：通过 drive meta API 获取飞书文档标题和最新修改时间
- 新增 `_guess_feishu_doc_type()`：根据 URL 路径推断文档类型（docx/wiki/sheet 等）
- API 优先、HTML 回退的双重解析策略
- 支持 `feishu_user_token` 凭证（credentials.yaml）

#### 微信公众号迁移处理（url_parser.py）
- 检测"账号已迁移"页面，自动提取新 URL 并重新解析
- 处理 `#rd` fragment 截断、`http→https` 转换、`&amp;` 解码

#### YouTube 日期解析修复（url_parser.py）
- 从页面 HTML 提取 `dateText.simpleText`（中文格式 "YYYY年MM月DD日"）
- 新增英文日期格式支持：`Feb 24, 2025`、`February 24, 2025`

#### 补丁程序
- `temp/patch_missing_urls.py`：扫描 WTA 文档，找出之前遗漏的纯文本二级 URL

### 变更

#### 错误日志目录迁移
- 所有程序的错误日志统一迁移到 `log-err/` 目录
- goMessage.py: duplicate_log.csv、parse_error_log.csv、bitable_fail_log.csv
- goAIPM.py: weekly_parse_error_log.csv
- goWTA.py: wta_errors_{date}.csv、urls/parsed CSV 输出文件（原 output/ 目录）

#### 字段名变更
- 多维表格列名 `大本营精选` → `精选合集`
- goWTA.py 写入值：`WaytoAGI` → `WTA-1`，`WTA_引用` → `WTA引用`

#### 标题回退逻辑
- 无意义标题（空、record、untitled）回退到 link_text 时，两端加全角圆括号：`（link_text）`

#### 重试机制优化
- 新增 `SKIP_RETRY_DOMAINS` 列表，统一管理跳过重试的域名
- 跳过域名：github.com、huggingface.co、127.0.0.1、localhost
- goWTA.py 和 goAIPM.py 均已应用

#### 凭证配置
- credentials.yaml 新增 `feishu_user_token` 字段
- credentials.yaml 新增 `docx:document:readonly` 权限 scope

### 修复
- 修复 goWTA.py 第一层重试索引指向错误的 bug
- 修复 goWTA.py `_extract_text_links` 只提取超链接、忽略纯文本 URL 的问题

---

## [1.4.0] - 2026-03-06

### 新增

#### 周报处理程序 goAIPM.py 修复与增强

##### URL 归一化去重
- 新增 `normalize_url()` 函数：去掉尾部斜杠 + 域名小写，用于去重比较
- 修复 `coze.cn` vs `coze.cn/`、`z.ai/` vs `Z.ai` 等重复问题
- 日报交叉检查阶段全面使用归一化 URL 匹配

##### 知识星球标题解析优先级修正
- ZSXQ 标题优先级调整为：`article.title`（长文章）> `text` 第一行 > `link_text`（fallback）
- 修复 `t.zsxq.com/dAFmA` 等链接使用短 link_text 而非完整 API 标题的问题
- 去除标题首尾的中文书名号【】

##### 文档分割线检测
- 周报文档提取 URL 时，遇到分割线（block_type=3）自动停止
- 分割线标志正文结束，其后的文章展开/附录区域不再被提取
- 修复 `X6JDPnOBcbr63k47MU8Q5A` 被错误提取为周报 URL 的问题

##### 错误日志改进
- `write_error_log()` 改为追加模式，不清空历史记录
- 新文件使用 `utf-8-sig`（BOM），追加使用 `utf-8`，Excel 兼容
- `周报时间` 列移至最后位置

#### 新增网站解析规则
- `huggingface.co/` → `Web-Huggingface`：通用解析

### 变更
- VERSION 更新至 1.4.0
- credentials.yaml 新增权限 scope：`wiki:wiki:readonly`（读取 wiki 文档内容）、`drive:drive.metadata:readonly`（获取文档元信息）

---

## [1.3.0] - 2026-03-05

### 新增

#### 多 profile 支持
- config.yaml 新增 `profiles` 配置段，支持多个群聊/表格组合
- `--profile <名称>` 参数切换 profile（默认使用第一个）
- 每个 profile 独立维护 `state`（last_processed_time 等），互不干扰
- `auth`、`feishu`、`bitable_columns`、`sort_config` 全局共用

#### 新增网站解析规则
- `https://arxiv.org/` → `Web-arxiv`：提取标题（去掉 `[id]` 前缀）和 `citation_date` 日期
- `https://clawdchat.cn/post/` → `Web_OT`：通用解析
- `https://dobby.now/community/view/` → `Web_OT`：通用解析，支持 `article:published_time`
- `https://cs.cloud.tencent.com/workbench/` → `Web_OT`：截断至 `&userId=` 前，JS渲染标记
- `youtube.com/watch?v` → `Web-Youtube`：截断至第一个 `&` 前，JS渲染标记
- `bilibili.com/` → `APP-哔哩哔哩`：扩展覆盖 opus 等非视频页面
- `bytedance.larkoffice.com/wiki/` → `飞书OT分享`：复用飞书解析
- `t.zsxq.com/` → `星球-AI产品经理大本营`：需登录，标记无法提取
- `articles.zsxq.com/` → `星球-AI产品经理大本营`：需登录，标记无法提取

#### 哔哩哔哩 opus 动态页解析
- 日期：从 `"pub_time":"YYYY年MM月DD日"` 中文格式提取
- 标题：`<title>` 含"的动态"时，改从 `"words"` 字段取正文第一行

### 变更

#### config.yaml 结构调整
- `auth` 节清理冗余字段（`access_token`、`refresh_token`、`token_expire_time`）
- `target_bitable` 删除 `url`（程序未使用）和 `wiki_token`（与 `app_token` 相同，统一用 `app_token`）
- `sort_config` 精简为仅保留 `priority_field`（其余字段程序未使用）
- `target_chat`、`target_bitable`、`state` 迁移至 `profiles` 下

#### 配置文件拆分
- `config.yaml` 拆分为 `config.yaml`（业务配置）+ `credentials.yaml`（敏感凭证）
- feishu_client.py 通过 `self.config` 和 `self.credentials` 分别访问
- token 刷新写回 credentials.yaml，profile/state 写回 config.yaml
- 两个文件均在 .gitignore 中

---



### 优化

#### 飞书 API 调用量优化
- **tenant_access_token 缓存**：获取的应用 token 缓存 2 小时（提前 5 分钟刷新），避免每次运行重复获取
- **批量写入多维表格**：使用 `batch_create_record` 接口，单次 API 调用写入最多 500 条记录（原为逐条写入）
- **限流/额度耗尽处理**：识别错误码 99991403（额度耗尽）和 429/99991400（限流），立即返回不重试，避免无效消耗

#### 预计效果
- 每日处理 20 条链接：API 调用从 ~35 次降至 ~8 次，节省约 77%
- 多维表格写入：从 N 次调用降至 1 次调用

### 新增
- `FeishuClient.batch_add_bitable_records()` 方法：批量添加多维表格记录
- `FeishuClient.QUOTA_EXHAUSTED_CODES` 常量：额度耗尽错误码集合
- `FeishuClient.RATE_LIMIT_CODES` 常量：限流错误码集合

### 参考
- 飞书 API 最佳实践：`D:\workspace\kbs\arqiao-shared-knowledge\skills\feishu-api-best-practices\SKILL.md`

---

## [1.1.1] - 2026-02-18

### 修复

#### Token 自动刷新
- feishu_client.py 新增统一请求方法 `_request()`，所有 API 调用自动检测 token 过期并刷新
- 识别的过期错误码：99991677、99991668、99991664
- 所有 API 方法重构为使用 `_request()`，不再各自处理 HTTP 请求

#### 多维表格日期字段写入
- 修复 `NumberFieldConvFail (code: 1254061)` 错误
- 日期字段从字符串 `"20260214"` 转为整数 `20260214`（该字段是 Number 类型，非 Date 类型）

#### CSV 日志编码统一
- 所有 CSV 文件读写编码从 `utf-8-sig` 统一为 `gbk`（与 Windows 环境一致）
- 修复 message_log.csv 中混合编码问题（第294-370行从 UTF-8 转为 GBK）
- 修复 parse_error_log.csv 多余空列（11列→8列）
- 修复 bitable_fail_log.csv 多余空列（9列→8列）
- 重建 duplicate_log.csv（从 message_log.csv 匹配恢复数据）

#### CSV 日志列补全
- 所有事件日志统一增加"星期"列
- 三个事件日志的公共列统一为：标题、日期、星期、链接、来源

### 改进
- API 写入失败时显示具体错误信息（通过 `client.last_error`）

#### CSV 写入 emoji 处理
- 修复 GBK 编码无法处理 emoji 导致的 `UnicodeEncodeError`
- 添加 `sanitize_for_gbk()` 函数，写入前自动过滤 emoji 等 Unicode 字符

---

## [1.1.0] - 2026-02-15

### 新增

#### 飞书多维表格写入
- 非重复数据自动写入飞书多维表格（wiki 下的 bitable）
- 写入前按发表日期排序
- 从多维表格读取已有 URL 进行去重
- 写入字段：标题、日期、星期、链接、来源
- CSV 重复但多维表格不存在的数据仍会写入多维表格

#### 事件日志
- 重复记录日志（`data/duplicate_log.csv`）：记录标题、日期、链接、来源、消息序号、记录时间
- 解析异常日志（`data/parse_error_log.csv`）：记录原始链接（截断前）、来源、异常信息、消息序号、记录时间
- 多维表格入库失败日志（`data/bitable_fail_log.csv`）：记录标题、日期、链接、来源、失败原因、消息序号、记录时间
- 追加写入模式，保留历史记录

#### 知乎问答解析
- 新增 `www.zhihu.com/question/` 链接识别，来源标识为 `APP-知乎`
- URL 自动截断追踪参数
- 受知乎 zse-ck v4 反爬限制，暂无法提取标题和日期

#### 观猹网站识别
- 新增 `watcha.cn/products/` 链接识别，来源标识为 `Web-观猹`
- 页面为 JS 渲染，暂无法提取标题和日期

#### 自动撤回消息
- 处理成功后自动撤回群聊中已处理的消息
- 撤回条件：只要消息被写入总日志（message_log.csv）就撤回

### 移除
- 移除 `--syncfeishutable` 参数及飞书电子表格同步功能（由多维表格替代）
- 移除 `--recall` 和 `--confirm-each` 参数（撤回改为自动执行）
- 移除 config.yaml 中的 `target_document` 和 `table_columns` 配置段
- 移除 `wiki:wiki:readonly` 权限要求（wiki 下的 bitable 的 app_token 与 wiki_token 相同）

### 变更
- feishu_client.py 新增 `get_bitable_records()` 方法
- article_parser.py 新增知乎问答解析规则和 `_parse_zhihu_question()` 方法
- config.yaml 新增 `target_bitable` 和 `bitable_columns` 配置段
- 备份脚本移至 `src/archive/` 目录

### 优化
- 延迟导入重型模块（requests、BeautifulSoup），启动时间从 ~800ms 优化到 ~70ms

### 修复
- 修复 token 刷新失败的问题：`refresh_access_token()` 缺少 `app_id` 和 `app_secret` 参数，导致每次都需要重新授权

---

## [1.0.0] - 2026-02-12

### 正式发布

首个正式版本，包含完整的飞书群消息整理工具功能。

### 功能列表

#### 消息读取
- 自动读取指定飞书群聊的消息
- 提取消息中的网络链接（支持文本消息和富文本消息）
- 支持增量处理（只处理新消息）
- 支持 `--all`、`--reset`、`--start`/`--end` 等命令行参数
- 支持 `--list-nolink` 查看无链接消息

#### 文章解析
- 微信公众号文章（标题、日期、公众号名称）
- 飞书OT分享文档（标题、更新日期，支持 wiki 和 docx 链接）
- 头条视频/文章（标题、日期，支持PC端和移动端URL）
- App-微博（来源识别，需登录限制）
- App-哔哩哔哩、知乎、小红书、即刻、华尔街见闻、小宇宙播客等
- APP-同花顺（标题、日期，通过API解析）
- URL 自动截断（去除追踪参数）

#### 表格写入
- 自动写入飞书电子表格
- 字段：消息序号、文章标题、发表日期、星期、链接URL、异常信息、来源、备注、是否重复、标记
- 写入失败自动重试（最多3次）

#### 重复检测
- 基于 URL 与表格已有记录比对
- 重复文章自动标记"重复"

#### Pin 标记
- 自动识别飞书群 Pin 消息
- Pin 消息在表格"标记"列写入"Pin"

#### 消息撤回
- 支持按序号撤回群聊消息（`--indices`）
- 倒序撤回、逐条确认（`--confirm-each`）、试运行（`--dry-run`）
- 主程序 `--recall` 参数：处理后自动撤回已转移消息
- 确认剩余全部（a）快捷操作

#### 授权鉴权
- OAuth 授权流程（`src/auth.py`）
- user_access_token 自动刷新
- refresh_token 30天有效期管理

---

## 开发历程

### 早期开发
- 基础消息读取和链接提取
- 微信公众号文章解析
- 飞书电子表格写入
- URL 截断规则（微信、微博、头条视频）
- App-微博日期和标题解析
- 头条视频日期和标题解析
- 飞书OT分享文档解析

### [20260210]
- 新增"头条OT"来源识别（`m.toutiao.com/w/`）
- 新增"APP-哔哩哔哩"来源识别（`m.bilibili.com/video/`）
- 完善飞书文档链接识别规则
- 修复多个链接解析失败问题
- 文章日期优先取最新更新日期

### [20260211]
- 新增 URL 重复检测功能
- 新增 Pin 消息标记功能
- 消息撤回脚本开发
- 撤回确认增加"a=确认剩余全部"功能
- 项目目录重组（src/、tests/、docs/ 分类）

### [20260212]
- 撤回验证去除3分钟等待
- 主程序新增 `--recall` 参数（处理后自动撤回）
- `delete_messages.py` 重命名为 `recall_messages.py`
- 全项目 delete → recall 术语统一
- PRD-代码全面核对与修复：
  - 表格写入增加3次重试逻辑（对齐 PRD US-04）
  - 修复 goMessage.py、auth.py、recall_messages.py 中的脚本路径提示
  - PRD 补充富文本消息处理说明和小宇宙OT来源
- 版本管理：发布 v1.0.0

### [20260213]
- 新增"APP-同花顺"来源识别（`.10jqka.com.cn/m/post`），通过 API 提取标题和日期
- 同花顺链接 URL 截断：保留 `?pid=xxx`，去除其余追踪参数
- 新增"飞书OT分享"对 `.feishu.cn/docx/` 链接的识别
- 飞书 wiki/docx 链接启用 URL 截断（保留"?"之前的内容）
- 修复 auth.py 回调服务器只处理单次请求的问题（改用 `serve_forever`）
