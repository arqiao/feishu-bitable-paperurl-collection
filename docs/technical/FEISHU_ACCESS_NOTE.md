# 飞书Wiki链接解析说明

## 问题

飞书Wiki链接（如 `https://forchangesz.feishu.cn/wiki/xxx`）需要登录才能访问完整内容。当前的解析器在未登录状态下只能获取到登录页面，无法提取文章的标题和更新日期。

## 时间字段优先级

当前的飞书解析方法已经按照以下优先级提取时间：

1. `update_time` - 最新更新时间（优先）
2. `create_time` - 创建时间（备用）
3. `updateTime` - 更新时间（备用）
4. `createTime` - 创建时间（备用）

这确保了对于可以持续更新的文档，我们提取的是最新的更新日期，而不是创建日期。

## 解决方案

### 方案1：使用飞书开放平台API

如果需要解析私有的飞书Wiki链接，建议使用飞书开放平台的API：

1. 在飞书开放平台创建应用
2. 获取 `app_id` 和 `app_secret`
3. 使用API获取文档内容和元数据

参考文档：https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview

### 方案2：配置Cookie

如果只是临时需要，可以在代码中配置登录后的Cookie：

```python
# 在 article_parser.py 的 __init__ 方法中添加
self.feishu_headers = {
    'User-Agent': 'Mozilla/5.0 ...',
    'Cookie': 'your_feishu_cookie_here'
}
```

### 方案3：手动输入

对于无法自动解析的飞书链接，可以手动输入更新日期。

## 示例

对于链接 `https://forchangesz.feishu.cn/wiki/Nndpw4zstiNpeKkA42KclGHanwc`：
- 如果有访问权限，解析器会自动提取最新的 `update_time`
- 如果没有访问权限，需要手动输入日期（如 20260208）

## 其他平台的时间提取策略

- **微信公众号**：提取 `publish_time`（发布时间）
- **头条**：提取 `publishTime`（发布时间）
- **B站**：提取 `pubdate`（发布日期）
- **小宇宙**：提取 `datePublished`（发布日期）
- **飞书Wiki**：优先提取 `update_time`（更新时间）

对于大多数平台，文章发布后不会更新，所以提取发布时间是合理的。只有飞书这类协作文档平台需要特别关注更新时间。
