# URL截断和解析改进说明

## 改进内容

### 1. URL截断功能

为了保持URL的简洁性和一致性，对以下类型的文章链接进行截断处理，只保留第一个"?"字符之前的内容：

| 文章来源 | 截断规则 | 示例 |
|---------|---------|------|
| 微信公众号 | 保留"?"之前 | `https://mp.weixin.qq.com/s/abc123?from=timeline` → `https://mp.weixin.qq.com/s/abc123` |
| App-微博 | 保留"?"之前 | `https://m.weibo.cn/status/5264065811448972?sourceType=weixin` → `https://m.weibo.cn/status/5264065811448972` |
| 头条_video | 保留"?"之前 | `https://m.toutiao.com/video/7591109484378849332/?app=news` → `https://m.toutiao.com/video/7591109484378849332/` |
| 头条OT | 保留"?"之前 | `https://m.toutiao.com/article/7578923974488064546/?app=news` → `https://m.toutiao.com/article/7578923974488064546/` |

**实现方式：**
- 在 `article_parser.py` 的 `site_rules` 中为每个规则添加第三个参数 `truncate_url`
- 在 `parse_url()` 方法中，匹配规则后根据 `truncate_url` 参数决定是否截断URL

### 2. App-微博解析改进

**目标：**
- 提取微博正文的第一行作为标题
- 提取发布日期

**实现方式：**
1. 尝试使用微博API (`https://m.weibo.cn/statuses/show?id={status_id}`) 获取JSON数据
2. 从JSON中提取：
   - `text` 字段：HTML格式的正文，提取纯文本后取第一行作为标题
   - `created_at` 字段：发布时间，格式如 "Fri Feb 07 12:34:56 +0800 2026"
3. 如果API需要登录，回退到HTML页面解析

**限制：**
- 微博内容需要登录才能访问
- 如果无法获取内容，会在 `error_info` 中记录"需要登录或无法访问"

**示例：**
```
URL: https://m.weibo.cn/status/5263671978887837
期望标题: 每天都有 AI 产品"炸裂"，但我已经不焦虑了
期望日期: 20260207
```

### 3. 头条_video解析改进

**目标：**
- 提取视频标题
- 提取发布日期

**实现方式：**
1. 使用移动端User-Agent访问页面
2. 从HTML页面的script标签中查找包含视频数据的JSON
3. URL解码处理（数据以URL编码格式存储）
4. 提取字段：
   - `title`：视频标题
   - `publishTime`：发布时间戳（秒或毫秒）
5. 支持PC端URL自动转换为移动端URL
6. 如果script中没有数据，尝试从meta标签提取

**成功案例：**
```
移动端URL: https://m.toutiao.com/video/7591109484378849332/
标题: 特斯拉一体压铸优点缺点都有#特斯拉#特斯拉modely #新能源汽车
日期: 20260103
星期: 周六

PC端URL: https://www.toutiao.com/video/7571827646221386292/
标题: 马斯克疯了：宣布把数据中心搬太空，彻底断掉地上电网依赖！
日期: 20251112
星期: 周三
```

### 4. 头条OT文章解析改进

**目标：**
- 提取文章标题
- 提取发布日期

**实现方式：**
1. 使用移动端User-Agent访问页面
2. 从HTML页面的script标签中查找包含文章数据的JSON
3. URL解码处理（数据以URL编码格式存储）
4. 提取字段：
   - `title`：文章标题
   - `publishTime`：发布时间戳（秒或毫秒）
5. 支持PC端URL自动转换为移动端URL
6. 与头条视频使用相同的解析逻辑

**成功案例：**
```
移动端URL: https://m.toutiao.com/article/7578923974488064546/
标题: 梁文锋署名论文，DeepSeek最强开源Agent模型炸场
日期: 20251202
星期: 周二

PC端URL: https://www.toutiao.com/article/7578923974488064546/
标题: 梁文锋署名论文，DeepSeek最强开源Agent模型炸场
日期: 20251202
星期: 周二
```

### 5. 飞书OT分享解析改进

**目标：**
- 提取文档标题
- 提取更新日期

**实现方式：**
1. 从HTML页面的script标签中查找包含文档数据的JSON
2. 采用两遍扫描策略：
   - 第一遍：查找同时包含 `title` 和有效 `update_time` 的script（优先）
   - 第二遍：如果第一遍没找到，只查找包含 `title` 的script
3. 验证时间戳有效性（必须大于2000-01-01）

**成功案例：**
```
URL: https://waytoagi.feishu.cn/wiki/PPniw6JDKiJMgTkQAJtckyv6nYd
标题: DemoDay：新手小白也能做出来的保姆级coding教程
日期: 20260207
星期: 周六
```

## 技术细节

### URL截断实现

```python
# 在 parse_url 方法中
if should_truncate and '?' in url:
    url = url.split('?')[0]
    result['url'] = url
```

### 微博API调用

```python
status_id = url.split('/')[-1].split('?')[0]
api_url = f'https://m.weibo.cn/statuses/show?id={status_id}'
response = requests.get(api_url, headers=self.mobile_headers, timeout=10)
data = response.json()
```

### 头条视频和文章解析

```python
# PC端URL自动转换
if 'https://www.toutiao.com/' in url:
    url = url.replace('https://www.toutiao.com/', 'https://m.toutiao.com/')

# 使用移动端User-Agent
response = requests.get(url, headers=self.mobile_headers, timeout=10)

# URL解码和JSON解析
decoded = unquote(script.string)
data = json.loads(decoded)

# 提取数据
if 'articleInfo' in data:
    title = data['articleInfo']['title']
    timestamp = data['articleInfo']['publishTime']
    # 处理字符串类型的时间戳
    if isinstance(timestamp, str):
        timestamp = int(timestamp)
```

### 飞书文档两遍扫描

```python
# 第一遍：查找同时包含标题和有效时间戳的script
for script in scripts:
    if '"title"' in script.string and 'update_time' in script.string:
        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', script.string)
        time_match = re.search(r'"update_time"\s*:\s*(\d{10,})', script.string)
        if title_match and time_match:
            # 验证时间戳有效性
            if timestamp > 946684800:  # 2000-01-01
                # 使用这个script的数据
                break

# 第二遍：如果第一遍没找到，只查找标题
if not result['title']:
    for script in scripts:
        if '"title"' in script.string:
            # 提取标题
```

## 测试结果

### URL截断测试
- ✅ 微信公众号：通过
- ✅ App-微博：通过
- ✅ 头条_video：通过
- ✅ 头条OT：通过

### 解析测试
- ✅ 头条视频（移动端）：成功提取标题和日期
- ✅ 头条视频（PC端）：成功提取标题和日期
- ✅ 头条文章（移动端）：成功提取标题和日期
- ✅ 头条文章（PC端）：成功提取标题和日期
- ✅ 飞书OT分享：成功提取标题和日期
- ⚠️ App-微博：需要登录，无法获取内容

## 注意事项

1. **微博的限制**
   - 微博有严格的反爬虫机制
   - 需要登录才能访问完整内容
   - 如果需要完整解析，建议使用以下方案：
     - 使用Selenium等浏览器自动化工具
     - 配置Cookie进行认证访问
     - 使用官方API（如果有）

2. **头条视频和文章解析**
   - 使用移动端User-Agent可以获取更完整的数据
   - 数据以URL编码的JSON格式存储在script标签中
   - 支持PC端URL自动转换为移动端URL
   - publishTime字段可能是字符串类型，需要类型转换

3. **飞书文档解析**
   - 飞书文档的HTML中包含多个script标签，其中有些包含不完整或错误的数据
   - 采用两遍扫描策略可以提高准确性
   - 时间戳验证很重要，避免提取到无效的数字

4. **URL截断的影响**
   - 截断后的URL更简洁，便于去重
   - 不影响文章的访问（参数通常用于追踪来源）
   - 存储到表格中的URL是截断后的版本

## 后续改进建议

1. **微博解析**
   - 添加Cookie支持，尝试登录访问
   - 使用Selenium处理动态内容
   - 考虑使用微博开放平台API

2. **通用改进**
   - 添加重试机制
   - 添加缓存机制，避免重复请求
   - 添加更详细的日志记录
   - 支持更多网站的解析

---

## 已知限制（续）

### 知识星球（zsxq.com）需登录（2026-03-05）

- `t.zsxq.com/` 短链和 `articles.zsxq.com/` 文章页均需登录才能访问内容
- 来源识别为 `星球-AI产品经理大本营`，标记 error_info，进入解析异常日志
- 建议手动在多维表格补录

### 腾讯企点（cs.cloud.tencent.com/workbench/）JS渲染（2026-03-05）

- 页面为 JS 渲染，curl 只能获取空 title
- URL 截断至 `&userId=` 之前（去除个人身份参数）
- 来源识别为 `Web_OT`，标记 JS渲染无法提取

### YouTube JS渲染（2026-03-05）

- `youtube.com/watch?v` 页面日期通过 JS 动态加载，curl 无法获取
- URL 截断至第一个 `&` 前（保留 `?v=xxx`）
- 来源识别为 `Web-Youtube`，标记未提取日期，标题可通过通用解析获取

### 哔哩哔哩 opus 动态页（2026-03-05）

- `m.bilibili.com/opus/` 页面 `<title>` 只有作者名（如"卢诗翰的动态"），无意义
- 日期字段为中文格式 `"pub_time":"2026年02月12日 21:34"`，非时间戳
- 正文内容在 `"words"` 字段（非 `"content"`）
- 解决方案：检测 title 含"的动态"时，从 `words` 字段取第一行作为标题；日期用 `_parse_date()` 解析中文格式

### arXiv 标题前缀（2026-03-05）

- `arxiv.org` 页面 `<title>` 格式为 `[2601.03220] 论文标题`
- 日期在 `<meta name="citation_date" content="2026/01/06">` 中，格式 `YYYY/MM/DD`
- 解决方案：正则去掉 `[id]` 前缀；日期替换 `/` 为 `-` 后用 `_parse_date()` 解析



知乎 `www.zhihu.com/question/` 类型页面有严格的反爬机制，当前无法提取标题和日期：

**尝试过的方案：**
- 直接 requests.get 返回 403，页面内容是 JS 验证脚本（zse-ck v4）
- PC UA、移动端 UA、完整浏览器 headers 均返回 403
- Session 方式（先访问首页获取 cookie 再访问）仍然 403
- 知乎 API `/api/v4/questions/{id}` 需要 x-zse-96 签名，返回 403（code:10003）
- answers 接口触发反爬验证（code:40352），需要人机验证
- 部分不需要签名的接口能返回 200，但不包含问题标题和日期
- noembed/archive.org 均无法获取

**当前处理：**
- 仅识别来源为 `APP-知乎`
- URL 截断追踪参数
- 记录错误信息 "知乎反爬限制、未提取标题、未提取日期"

**可能的解决方案：**
- cloudscraper
- playwright/selenium 浏览器自动化
- 从飞书消息的分享卡片中提取标题信息

### 观猹 watcha.cn 页面 JS 渲染（2026-02-15 调研）

观猹 `watcha.cn/products/` 类型页面为纯前端 SPA，服务端返回的 HTML 不包含产品信息：

**问题分析：**
- 服务端返回固定的 HTML 模板，`<title>` 标签为通用的 "观猹丨玩 AI，上观猹！"
- 产品标题、描述等信息通过 JS 动态加载
- 尝试多种 API 路径（/api/、/trpc/、/server/）均返回 SPA 的 HTML

**当前处理：**
- 仅识别来源为 `Web-观猹`
- 不尝试提取标题（避免返回错误的通用标题）
- 记录错误信息 "JS渲染页面、未提取标题、未提取日期"

**可能的解决方案：**
- playwright/selenium 浏览器自动化渲染页面后提取
