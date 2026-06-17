# 多维表格物理排序工具指南

`src/reorderMain.py` 用于按配置规则重排飞书多维表格记录的物理顺序。它不会修改排序字段本身，而是通过“创建新记录到表尾 → 更新父记录引用 → 删除旧记录”的方式，让表格默认顺序变成目标顺序。

## 适用场景

- 表格需要按日期、主题分类、企业组织、父子层级、兴趣优先级等字段形成稳定顺序。
- 需要保持同一家族树的父子关系，并让同一根 URL 的一级节点相邻。
- 需要在不依赖旧迁移项目文件的情况下，直接在本项目内运行排序工具。

## 配置

配置位于 `cfg/config.yaml` 的 `reorderBitable` 段，模板见 `cfg/config.yaml.template`。

```yaml
reorderBitable:
  target_table:
    app_token: LOBswegeNiQIf4kPDAtcJDbhnTg
    table_id: tblxxxxxxxxxxxxxx
  sort_config:
    category_field: 主题分类
    date_field: 日期
    date_range:
      start: 20240101
      end: 20260831
    org_field: 企业组织
    parent_field: 父记录
    preview_count: 100
    priority_field: 兴趣优先级
    title_field: 标题
    url_field: 链接
    collection_field: 精选合集
```

关键字段：

- `target_table.app_token` / `table_id`：目标多维表格。
- `date_range.start` / `end`：只移动日期范围内的记录，范围外记录保持原位。
- `parent_field`：父记录字段，程序优先读取飞书返回的 `record_ids` 精准识别父节点。
- `url_field`：链接字段，必须能提取真实 `http(s)` URL；程序不会把链接显示标题当作 URL。
- `collection_field`：精选合集字段，用于重复链接记录的身份区分。

## 运行命令

默认只预览，不写表：

```bash
python src/reorderMain.py
```

显示范围内目标顺序明细：

```bash
python src/reorderMain.py --show-records
```

执行排序：

```bash
python src/reorderMain.py --execute
```

限制每批临时新增记录数：

```bash
python src/reorderMain.py --execute --max-temp-records 1500
```

查看参数帮助：

```bash
python src/reorderMain.py --help
```

## 排序规则

工具先按家族树处理记录：

- 根记录参与排序，子记录跟随自己的根记录移动。
- 家族树内部保持原表中的相对顺序，避免打散父子层级。
- 日期范围外记录保持原位。
- 范围内根记录按配置字段排序：日期、主题分类、企业组织、兴趣优先级、标题。
- 根记录 URL 相同但分类不同的场景，会以第一次出现的分类作为排序依据，并保证相同 URL 的一级节点相邻。
- 子节点 URL 重复不会参与全局根节点聚类，父子关系以父记录引用为准。

## 执行算法

执行时采用分批的三步流程：

1. 批量创建当前批次的新记录。
2. 批量更新新记录中的父记录字段，把旧父节点 ID 替换为新父节点 ID。
3. 批量删除该批次的旧记录。

为了避免飞书多维表格单表 20000 条记录上限，程序会自动计算可用临时空间：

```text
可用临时新增数 = 20000 - 当前记录数 - 200
```

其中 200 是默认安全余量。程序会按完整家族树切分批次，不会把同一个家族树拆到不同批次。如果单个家族树超过可用临时空间，程序会停止并提示调小日期范围或清理表格空间。

## 安全检查

执行前会做以下检查：

- 获取字段定义和原始记录失败时停止。
- 父记录字段优先使用 `record_ids`；无法唯一识别父节点时停止。
- 链接字段非空但无法提取真实 URL 时停止执行。
- 新旧记录映射无法确定时停止。
- 批量创建、批量更新或批量删除失败时停止。
- 执行后会重新读取表格做顺序和父子关系验证。

## API 调用量

一次预览通常只读数据，不写表：

- 读取字段：约 1 次。
- 读取记录：按分页计算，约 `ceil(记录数 / 500)` 次。

一次执行会额外产生写操作。设移动记录数为 `N`，父记录需要回填的记录数为 `P`，批次数为 `B`：

- 创建记录：约 `ceil(N / 500)` 次；少数重复身份记录可能单条创建。
- 更新父记录：约 `ceil(P / 500)` 次。
- 删除旧记录：约 `ceil(N / 500)` 次。
- 执行后验证读取：约 `ceil(记录数 / 500)` 次。

`--max-temp-records` 会增加批次数，但不会显著改变创建/删除总调用量；它的主要作用是控制同一时刻临时新增记录数，避免撞到单表记录上限。

## 测试

```bash
python -m py_compile src/reorderMain.py src/reorderSorter.py src/reorderTreeBuilder.py src/feishu_client.py tests/test_reorder.py
python -m unittest tests/test_reorder.py
```
