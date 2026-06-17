# 架构设计说明

## 核心设计理念

本程序采用**规则驱动**的架构设计，基于 URL 特征识别不同网站来源，并应用相应的解析策略。这种设计使得程序可以轻松扩展到支持几十种甚至更多网站。

## 架构组件

### 1. URL 解析器 (url_parser.py)

#### 核心数据结构：site_rules

```python
self.site_rules = [
    ('URL特征字符串', ('来源名称', 解析方法)),
    # 示例：
    ('mp.weixin.qq.com', ('微信', self._parse_wechat_article)),
    ('https://b23.tv/', ('APP-哔哩哔哩', self._parse_general_article)),
]
```

#### 工作流程

```
URL 输入
  ↓
遍历 site_rules 查找匹配
  ↓
找到匹配 → 应用对应的解析方法
  ↓
未找到 → 使用通用解析方法 + 标记"未提取来源信息"
  ↓
返回解析结果
```

#### 解析方法分类

1. **特定网站解析方法**
   - `_parse_wechat_article()` - 微信公众号（提取公众号名称、发布时间；支持账号迁移自动跳转）
   - `_parse_xiaohongshu_article()` - 小红书（需要登录）
   - `_parse_weibo_article()` - 微博（需要登录）
   - `_parse_feishu_article()` - 飞书文档（wiki/docx，API 优先 + HTML 回退）
   - `_parse_feishu_record()` - 飞书多维表格记录（从 shareRecord JSON 提取）
   - `_parse_feishu_base()` - 飞书多维表格首页（从 SERVER_DATA.meta 提取）
   - `_parse_toutiao_article()` - 头条文章（提取标题和日期）
   - `_parse_toutiao_video_article()` - 头条视频（提取标题和日期）
   - `_parse_bilibili_article()` - 哔哩哔哩视频（提取标题和日期）
   - `_parse_xiaoyuzhou_article()` - 小宇宙播客（提取标题和日期）
   - `_parse_10jqka_article()` - 同花顺（通过API提取标题和日期）
   - `_parse_youtube_article()` - YouTube（从 dateText 提取日期）
   - `_parse_zsxq_article()` - 知识星球（需登录，API 提取，5 次指数退避重试）
   - `_parse_zsxq_garden()` - 知识星球 garden 页面（强制 UTF-8 编码，确保尾部斜杠）
   - `_parse_shimo()` - 石墨文档（动态 API 调用 `lizard-api/files/{doc_id}`）
   - `_parse_xiaobot_article()` - 小报童（API + MD5 签名认证，支持 post/paper 两种路径）
   - `_parse_jike_article()` - 即刻（div 优先 + og:title 回退，时间前缀支持天/小时/月/年/分钟）

2. **通用解析方法**
   - `_parse_general_article()` - 适用于大多数网站
   - 提取 title、meta 标签、time 标签等通用元素

### 2. 飞书客户端 (feishu_client.py)

负责与飞书 API 交互，使用两种身份调用不同接口：

**配置来源**：`cfg/config.yaml` 保存业务配置；飞书应用密钥、Token、LLM Key 等敏感信息通过本机密钥配置加载，避免提交到仓库。

**应用身份**（`tenant_access_token`，通过 app_id + app_secret 获取，**带缓存**有效期 2 小时）：
- `get_chat_messages()` — 获取群聊消息
- `get_pin_messages()` — 获取 Pin 消息列表

**用户身份**（`user_access_token`，2 小时过期，需通过 refresh_token 刷新）：
- `get_chat_list()` / `find_chat_by_name()` — 获取群聊列表
- `get_spreadsheet_*()` / `append_spreadsheet_values()` — 电子表格读写
- `get_bitable_*()` / `get_raw_bitable_records()` / `batch_add_bitable_records()` / `batch_update_bitable_records()` / `batch_delete_bitable_records()` — 多维表格操作
- `recall_message()` — 撤回消息

**API 调用保护**：
- 额度耗尽（错误码 99991403）：立即返回，不重试
- 限流（错误码 429/99991400）：立即返回，不重试
- Token 过期：自动刷新后重试
- 全局超时：`_request()` 默认 30s timeout，防止网络异常导致程序挂起

### 3. 主程序

#### goMessage.py（群消息处理）
- 命令行参数解析
- 消息提取和 URL 识别
- 调用 URL 解析器
- 写入飞书多维表格
- 状态管理（增量更新）

#### goAIPM.py（周报处理）
- `--file <url>`：从飞书 wiki 周报文档提取 URL → 解析 → 日报交叉检查 → 写入多维表格
- `--list <file>`：批量处理周报文档列表
- `--daily <url>`：处理单个日报文档 → 解析 → 写入多维表格
- `--update`：自动查找新日报（基于 `last_processed_date`），逐篇处理
- `--weekly <url>`：基于周报 wiki 完善多维表格（更新已有记录 + 新增记录）
- 精选合集层级：PA-日周++ > PA-周++ > PA-日++ > PR_引用 > PB-日周 > PB-周 > PB-日
- 所有模式均接入 `bitable_url_cache` 缓存更新

#### goWTA.py（WaytoAGI 知识库处理）
- 两层 URL 提取（知识库文档 → wiki 子页面外部链接）
- 父记录关联（L2 挂 L1，原文链接例外：orig 成为顶层父记录）
- 无效 URL 提取阶段过滤
- 重试机制（跳过结构性失败域名）
- `--his` 历史批量 / `--update` 增量更新
- `--update` 模式去重优化：
  - L1 通过 `processed_urls` / `dedup_keys` 判断已存在/已去重，跳过 `parse_url`
  - L2 通过缓存子节点清单去重：根据 CSV 缓存中的父子关系（`parent_children`、`cache_id_to_norm`、`norm_to_parent_id`），构建已知子节点 URL 集合，逐个比对新二级 URL
  - 所有二级 URL 均在缓存中时，L1 整体跳过
  - URL 比较使用 `_clean_url_for_dedup()`：飞书去参数 + 微信截断 + normalize
- 本地缓存 `wta_processed_urls.csv`：记录 id、url、layer、parent_id、精选日期等

#### autoClassify.py（自动分类）
- 利用 LLM（DeepSeek）对多维表格中的文章记录自动分类打标签
- 从参考表（sample_tables）学习已有分类模式，对目标表（target_table）中未分类记录进行批量分类
- 批量处理：按 `batch_size` 分批读取未分类记录 → 构建 LLM prompt → 调用 LLM API → 解析分类结果 → 写回多维表格
- 支持试运行模式（`--dry-run`），仅预览分类结果不写入
- 配置项：LLM 提供商/模型/参数、参考表列表、目标表、批量大小

#### reorderMain.py（多维表格物理排序）
- 读取 `cfg/config.yaml` 的 `reorderBitable` 段，默认只预览目标顺序
- 通过 `get_raw_bitable_records(..., text_field_as_array=True, display_formula_ref=True)` 获取包含原始字段结构的记录，保留父记录字段中的 `record_ids`
- `reorderTreeBuilder.py` 构建父子关系树，按完整家族树处理记录
- `reorderSorter.py` 计算目标顺序：日期范围外保持原位，范围内根记录按日期、主题分类、企业组织、兴趣优先级、标题排序
- 根记录 URL 相同的记录保持相邻；如果分类不同，采用首次出现记录的分类作为排序依据
- `--execute` 采用“批量创建新记录 → 批量更新父记录 → 批量删除旧记录”改变物理顺序
- 执行时按家族树分批，自动控制临时新增记录数，避免超过飞书单表 20000 条记录上限
- 链接字段审计要求提取真实 `http(s)` URL，避免把飞书显示标题误当作链接值

## 扩展性设计

### 添加新网站支持

**步骤 1：添加识别规则**

在 `url_parser.py` 的 `__init__` 方法中添加：

```python
self.site_rules = [
    # ... 现有规则 ...
    ('新网站URL特征', ('新网站名称', self._parse_general_article)),
]
```

**步骤 2：（可选）创建专用解析方法**

如果网站需要特殊的解析逻辑：

```python
def _parse_newsite_article(self, url: str, result: Dict) -> Dict:
    """解析新网站文章"""
    try:
        response = requests.get(url, headers=self.headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        # 自定义提取逻辑
        result['title'] = soup.find('h1', class_='article-title').get_text()
        # ... 其他字段提取 ...

        return result
    except Exception as e:
        result['error_info'] = f'新网站解析失败: {str(e)}'
        return result
```

**步骤 3：更新规则引用**

```python
('新网站URL特征', ('新网站名称', self._parse_newsite_article)),
```

### 规则匹配优先级

规则按照在 `site_rules` 列表中的顺序进行匹配，**第一个匹配的规则生效**。

因此：
- 更具体的规则应该放在前面
- 更通用的规则应该放在后面

示例：
```python
# 正确顺序
('https://m.toutiao.com/video/', ('头条_video', ...)),  # 具体
('https://m.toutiao.com/article/', ('头条OT', ...)),    # 具体
('toutiao.com', ('头条', ...)),                         # 通用

# 错误顺序（会导致 video 和 article 都被识别为"头条"）
('toutiao.com', ('头条', ...)),                         # 通用规则在前
('https://m.toutiao.com/video/', ('头条_video', ...)),  # 永远不会匹配
```

## 数据流

```
飞书群聊消息
  ↓
提取 URL (goMessage.py)
  ↓
URL 识别 (url_parser.py)
  ↓
应用解析规则
  ↓
提取文章信息（标题、日期、来源）
  ↓
写入飞书表格 (feishu_client.py)
  ↓
更新处理状态 (cfg/config.yaml / 本机密钥配置)
```

## 配置管理

### 配置来源

项目采用“仓库内业务配置 + 本机密钥配置”的拆分方式：
- `cfg/config.yaml` — 业务配置（profiles、bitable_columns、sort_config、weekly_report、waytoagi、reorderBitable、auto_classify 等）
- 本机密钥配置 — 敏感凭证（飞书 app 凭证、auth token、zhihu cookie、zsxq token、LLM Key 等）

`feishu_client.py` 通过 `self.config` 访问业务配置，通过 `self.credentials` 访问本机密钥配置。Token 刷新写回本机密钥配置，profile/state 等业务状态写回 `cfg/config.yaml`。

### cfg/config.yaml 结构

```yaml
target_chat:
  name: "群聊名称"
  chat_id: "..."

target_document:
  token: "..."
  sheet_id: "..."

state:
  last_processed_time: 0
  last_run_time: "..."

table_columns:
  title: "文章标题"
  publish_date: "文章发表日期"
  # ... 其他列映射 ...

reorderBitable:
  target_table:
    app_token: "..."
    table_id: "..."
  sort_config:
    date_field: "日期"
    parent_field: "父记录"
    url_field: "链接"
```

## 错误处理策略

1. **网络错误**：记录到 `remark` 字段
2. **解析失败**：记录到 `error_info` 字段
3. **缺失信息**：在 `error_info` 中列出（如"未提取日期"）
4. **未识别网站**：标记"未提取来源信息"

## 性能优化

- **批量写入多维表格**（使用 `batch_create_record` 接口，单次最多 500 条）
- **多维表格物理排序分批执行**：按完整家族树切分批次，默认按 20000 单表上限和 200 条安全余量计算临时新增空间
- **tenant_access_token 缓存**（有效期 2 小时，提前 5 分钟刷新）
- **限流/额度保护**（识别错误码立即返回，不重试）
- **全局 HTTP 超时**（`feishu_client._request` 默认 30s，防止无限等待）
- **goWTA.py --update 去重优化**：缓存子节点清单预判断，跳过已知重复 URL 的 `parse_url` 调用
- **goAIPM.py zsxq 重试**：5 次指数退避 + 批量补重试，提高知识星球 API 成功率
- 请求间隔控制（避免触发限流）
- 增量更新（只处理新消息）
- Token 自动刷新

## 测试

运行测试脚本验证 URL 识别：

```bash
python tests/test_parser.py
python -m unittest tests/test_reorder.py
```

## 未来扩展方向

1. **支持更多网站**：持续添加新的识别规则
2. ~~**智能解析**：使用 AI 提取文章信息~~ → 已由 autoClassify.py 实现 LLM 驱动的自动分类
3. **并发处理**：多线程/异步处理提高速度
4. **缓存机制**：避免重复解析相同 URL
5. **配置文件化**：将 site_rules 移到配置文件
