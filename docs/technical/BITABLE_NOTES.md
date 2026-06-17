# 飞书多维表格（Bitable）开发笔记

## Wiki 下的 Bitable

### app_token 与 wiki_token 的关系

对于 wiki 知识库下的多维表格，有一个重要发现：

**app_token 与 wiki_token 相同**

示例 URL：
```
https://xxx.feishu.cn/wiki/LOBswegeNiQIf4kPDAtcJDbhnTg?table=tbl4uAOEiXGMae9c
```

- `wiki_token`: `LOBswegeNiQIf4kPDAtcJDbhnTg`
- `app_token`: `LOBswegeNiQIf4kPDAtcJDbhnTg`（相同）
- `table_id`: `tbl4uAOEiXGMae9c`

### 不需要 wiki:wiki:readonly 权限

最初以为需要通过 wiki API 的 `get_node` 接口获取 bitable 的实际 `obj_token`（即 app_token），因此申请了 `wiki:wiki:readonly` 权限。

**实际情况**：
- wiki 下的 bitable，其 app_token 就是 wiki_token
- 可以直接在配置文件中填写，无需调用 wiki API
- 只需要 `bitable:app` 和 `bitable:app:readonly` 权限即可

### 配置示例

```yaml
target_bitable:
  url: "https://xxx.feishu.cn/wiki/LOBswegeNiQIf4kPDAtcJDbhnTg?table=tbl4uAOEiXGMae9c"
  wiki_token: "LOBswegeNiQIf4kPDAtcJDbhnTg"
  app_token: "LOBswegeNiQIf4kPDAtcJDbhnTg"  # 与 wiki_token 相同
  table_id: "tbl4uAOEiXGMae9c"
```

## 权限要求

### 最小权限集

操作多维表格只需要以下权限：
- `bitable:app` - 读写多维表格
- `bitable:app:readonly` - 读取多维表格

### 不需要的权限

- `wiki:wiki:readonly` - 不需要（除非要获取 wiki 节点的其他信息）

## API 调用

### 读取记录

```http
GET https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records
Authorization: Bearer {user_access_token}

Query Parameters:
- page_size: 500 (最大)
- field_names: ["字段1", "字段2"] (JSON 格式)
- page_token: (分页)
```

### 添加记录

```http
POST https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records
Authorization: Bearer {user_access_token}
Content-Type: application/json

{
  "fields": {
    "标题": "文章标题",
    "日期": "20260215",
    "链接": "https://..."
  }
}
```

### 批量创建、更新和删除记录

多维表格物理排序工具使用批量接口减少 API 调用：

- 批量创建：`batch_create_record`，单次最多 500 条。
- 批量更新：`batch_update_record`，单次最多 500 条。
- 批量删除：`batch_delete_record`，单次最多 500 条。

排序工具不会直接“移动”记录，因为飞书没有提供调整物理顺序的专用接口。当前做法是：

1. 按目标顺序创建新记录。
2. 把新记录中的父记录字段从旧父节点 ID 更新为新父节点 ID。
3. 删除旧记录。

父记录字段在显示层面可能是标题文本，但 API 原始返回值中会包含类似下面的 `record_ids`，应优先使用它识别父节点：

```json
[
  {
    "record_ids": ["recxxxxxxxxxxx"],
    "table_id": "tblxxxxxxxxxxx",
    "text": "父记录标题",
    "type": "text"
  }
]
```

### 单表记录数上限

飞书多维表格单表记录总数不能超过 20000 条。物理排序执行时会临时新增记录，因此如果当前表已有 18000 条，不能一次复制数千条记录。

`src/reorderMain.py` 会自动计算临时空间：

```text
可用临时新增数 = 20000 - 当前记录数 - 200
```

然后按完整家族树分批执行，确保同一个家族树不会被拆开。如果单个家族树超过可用临时空间，需要缩小日期范围或先清理表格空间。

## 踩坑记录

### 1. app_token 被误清空

**问题**：`cfg/config.yaml` 中的 `app_token` 被意外清空，导致程序尝试调用 wiki API 获取，但没有 `wiki:wiki:readonly` 权限而失败。

**解决**：直接在配置中填写 app_token（与 wiki_token 相同），不依赖 wiki API。

### 2. 去重逻辑

**需求**：
- CSV 去重：用于标记"是否重复"列
- Bitable 去重：用于决定是否写入多维表格

**关键点**：这两个去重是独立的。
- 记录可能在 CSV 中标记为重复（之前处理过），但不在 bitable 中（之前写入失败）
- 这种情况下仍需要写入 bitable

**正确逻辑**：
```python
# 写入 bitable 的条件：URL 不在 bitable 已有记录中
if article_info['url'] not in bitable_existing_urls:
    bitable_rows.append(...)
```

### 3. 撤回消息的安全检查

**问题**：消息在 CSV 中标记为重复，但实际从未写入 bitable，程序却撤回了该消息。

**原因**：撤回逻辑只检查了 CSV 重复状态，没有检查 bitable。

**正确逻辑**：
```python
# 只有当消息的所有 URL 都在 bitable 中才撤回
for mid, urls in msg_urls.items():
    if all(u in bitable_existing_urls for u in urls):
        recall_ids.append(mid)
```

### 4. 日期字段是数字类型，不是日期类型（NumberFieldConvFail）

**问题**：写入多维表格时报错 `NumberFieldConvFail (code: 1254061)`。

**原因**：多维表格中"日期"列的字段类型是 `Number`（数字），不是 `Date`（日期）。已有数据存储的是 `20251001` 这样的 YYYYMMDD 整数。代码传的是字符串 `"20260214"`，数字字段无法接受字符串。

**解决**：写入前将日期字符串转为整数：
```python
if key == 'publish_date' and isinstance(value, str) and value.isdigit():
    value = int(value)
```

**注意**：不要转成毫秒时间戳，这个字段不是日期类型。

**飞书字段类型参考**（type 值）：
- 1 = Text（文本）
- 2 = Number（数字）
- 3 = SingleSelect（单选）
- 4 = MultiSelect（多选）
- 5 = DateTime（日期时间，这个才需要毫秒时间戳）

### 5. 链接字段可能返回富文本结构

**问题**：飞书多维表格的链接字段在显示层面可能展示标题文本，API 返回值也可能是富文本数组。如果只取 `text`，会把链接标题误当成 URL，导致重复记录映射错误。

**解决**：
- 读取原始记录时保留字段结构。
- 从字段值中提取真实 `http(s)` URL，检查 `link`、`url`、`href`、`text` 等可能位置。
- 排序工具执行前做链接字段审计：链接字段非空但无法提取真实 URL 时停止。
- 不把显示标题作为 URL fallback。

### 6. 父记录字段不要用标题文本反查

**问题**：父记录字段显示的是标题文本，同名标题或重复 URL 场景下，用标题反查会把子节点挂到错误父节点。

**解决**：
- 优先使用 API 原始返回中的 `record_ids`。
- 只有在确实没有 `record_ids` 时才进入兼容逻辑，并且无法唯一识别时停止执行。
- 创建新记录后统一批量更新父记录字段，把旧父节点 ID 映射为新父节点 ID。

## 参考文档

- [多维表格概述](https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview)
- [获取记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list)
- [新增记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/create)
- [多维表格物理排序工具指南](../guides/REORDER_BITABLE_GUIDE.md)
