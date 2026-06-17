"""
飞书多维表格记录物理排序工具。

默认只预览目标顺序；传入 --execute 后才会执行复制和删除。
"""

# 运行参数说明：
#   python src/reorderMain.py
#       只读取表格、计算目标顺序并打印预览，不写入、不删除记录。
#   python src/reorderMain.py --execute
#       打印预览后直接执行复制和删除，不再二次询问 y/n。
#   python src/reorderMain.py --help
#       查看命令行参数帮助。

import argparse
import os
import sys
import time
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from feishu_client import FeishuClient
from reorderSorter import Sorter
from reorderTreeBuilder import TreeBuilder


SKIP_FIELDS = {
    '_id', '_record_id', 'record_id', 'created_time', 'last_modified_time',
    'created_by', 'last_modified_by'
}


class ReorderBitable:
    """把本项目 FeishuClient 适配成排序工具需要的表格操作。"""

    def __init__(self, client: FeishuClient, app_token: str, table_id: str):
        self.client = client
        self.app_token = app_token
        self.table_id = table_id

    def get_table_fields(self) -> List[Dict]:
        fields = self.client.get_bitable_fields(self.app_token, self.table_id)
        if not fields and self.client.last_error:
            print("错误：获取字段定义失败，请检查表格权限")
            sys.exit(1)
        return fields

    def get_field_options(self, fields: List[Dict], field_name: str) -> List[str]:
        for field in fields:
            if field.get('field_name') == field_name:
                options = field.get('property', {}).get('options', [])
                return [opt.get('name') for opt in options]
        return []

    def get_all_records(self) -> List[Dict]:
        records = self.client.get_raw_bitable_records(self.app_token, self.table_id)
        if not records and self.client.last_error:
            print("错误：获取记录失败，请检查表格权限")
            sys.exit(1)
        return records

    def create_record(self, fields: Dict) -> Optional[Dict]:
        result = self.client.batch_add_bitable_records(
            self.app_token, self.table_id, [fields]
        )
        if result.get('failed', 0) > 0 or not result.get('records'):
            return None
        return result['records'][0]

    def batch_create_records(self, records: List[Dict]) -> Dict:
        return self.client.batch_add_bitable_records(
            self.app_token, self.table_id, records
        )

    def batch_delete_records(self, record_ids: List[str]) -> bool:
        return self.client.batch_delete_bitable_records(
            self.app_token, self.table_id, record_ids
        )


def _display_value(value):
    if isinstance(value, dict):
        return value.get('text') or value.get('name') or str(value)
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return first.get('text') or first.get('name') or str(first)
        return str(first)
    return value


def print_preview(
    current_order: List[str],
    target_order: List[str],
    out_range_count: int,
    in_range_count: int,
    tree: TreeBuilder,
    date_field: str,
    title_field: str,
    preview_count: int = 100,
    in_range_ids: List[str] = None
) -> bool:
    """打印预览信息。"""
    print("\n" + "=" * 60)
    print("排序预览")
    print("=" * 60)

    total = len(current_order)
    root_count = len(tree.root_ids)
    need_move = current_order != target_order

    print("\n【统计摘要】")
    print(f"  记录总数: {total}")
    print(f"  根记录数: {root_count}")
    print(f"  范围外记录: {out_range_count} (保持原位)")
    print(f"  范围内记录: {in_range_count} (需排序)")
    print(f"  是否需要调整: {'是' if need_move else '否'}")

    if not need_move:
        print("\n当前顺序已符合目标，无需调整。")
        return False

    display_list = in_range_ids if in_range_ids else target_order
    show_count = min(preview_count, len(display_list))
    print(f"\n【范围内前{show_count}条目标顺序】")
    for i, record_id in enumerate(display_list[:preview_count]):
        record = tree.get_record(record_id)
        fields = record.get('fields', {}) if record else {}
        date = fields.get(date_field, '-')
        title = _display_value(fields.get(title_field, record_id[:8]))
        title = str(title)
        if len(title) > 30:
            title = title[:30] + "..."

        print(f"  {i + 1:3d}. [{date}] {title}")

    return True


def _prepare_fields(record: Dict, parent_field: str, number_fields: set,
                    parent_new_id: str = None) -> Dict:
    fields = {}
    for key, value in record.get('fields', {}).items():
        if key in SKIP_FIELDS:
            continue
        if key in number_fields and isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        fields[key] = value

    if parent_new_id:
        fields[parent_field] = [parent_new_id]
    else:
        fields.pop(parent_field, None)

    return fields


def copy_family_tree(bitable: ReorderBitable, tree: TreeBuilder, root_id: str,
                     parent_field: str, number_fields: set):
    """复制一个家族树到表尾，返回 (新旧 ID 映射, 需删除的原 ID 列表)。"""
    id_map = {}
    to_delete = []

    def has_children(old_id):
        return len(tree.children_map.get(old_id, [])) > 0

    def create_single(old_id, parent_new_id):
        record = tree.get_record(old_id)
        if not record:
            print(f"  警告：记录 {old_id} 不存在，跳过")
            return None

        fields = _prepare_fields(record, parent_field, number_fields, parent_new_id)
        new_record = bitable.create_record(fields)
        if not new_record:
            print(f"  错误：创建记录失败 (原ID: {old_id})")
            sys.exit(1)

        new_id = new_record.get('record_id')
        id_map[old_id] = new_id
        time.sleep(0.3)
        return new_id

    def create_batch_leaves(leaf_ids, parent_new_id):
        if not leaf_ids:
            return

        records_to_create = []
        old_ids_in_order = []
        for old_id in leaf_ids:
            record = tree.get_record(old_id)
            if record:
                records_to_create.append(
                    _prepare_fields(record, parent_field, number_fields, parent_new_id)
                )
                old_ids_in_order.append(old_id)

        if not records_to_create:
            return

        if len(records_to_create) == 1:
            new_record = bitable.create_record(records_to_create[0])
            if not new_record:
                print(f"  错误：创建记录失败 (原ID: {old_ids_in_order[0]})")
                sys.exit(1)
            id_map[old_ids_in_order[0]] = new_record.get('record_id')
        else:
            result = bitable.batch_create_records(records_to_create)
            if result.get('failed', 0) > 0:
                print(f"  错误：批量创建失败 {result['failed']} 条")
                sys.exit(1)
            for i, new_rec in enumerate(result.get('records', [])):
                id_map[old_ids_in_order[i]] = new_rec.get('record_id')

        time.sleep(0.3)

    def process_children(parent_old_id):
        parent_new_id = id_map.get(parent_old_id)
        children = tree.children_map.get(parent_old_id, [])
        pending_leaves = []

        for child_id in children:
            if has_children(child_id):
                if pending_leaves:
                    create_batch_leaves(pending_leaves, parent_new_id)
                    pending_leaves = []
                create_single(child_id, parent_new_id)
                process_children(child_id)
            else:
                pending_leaves.append(child_id)

        if pending_leaves:
            create_batch_leaves(pending_leaves, parent_new_id)

    root_record = tree.get_record(root_id)
    if not root_record:
        print(f"  警告：根记录 {root_id} 不存在")
        return {}, []

    root_fields = _prepare_fields(root_record, parent_field, number_fields, None)
    new_root = bitable.create_record(root_fields)
    if not new_root:
        print(f"  错误：创建根记录失败 (原ID: {root_id})")
        sys.exit(1)
    id_map[root_id] = new_root.get('record_id')
    time.sleep(0.3)

    process_children(root_id)

    def collect_delete_order(rid):
        for child_id in tree.children_map.get(rid, []):
            collect_delete_order(child_id)
        to_delete.append(rid)

    collect_delete_order(root_id)
    return id_map, to_delete


def execute_reorder(bitable: ReorderBitable, tree: TreeBuilder, sorter: Sorter,
                    in_range_records: List[str], number_fields: set,
                    date_field: str):
    """执行记录重排序：复制范围内记录到表尾，再删除原记录。"""
    parent_field = sorter.tree.parent_field
    target_order, _, _ = sorter.generate_target_order()

    in_range_root_ids = []
    seen = set()
    for rid in target_order:
        if rid in seen:
            continue
        seen.add(rid)
        root = tree.get_root_record_id(rid)
        if root == rid and rid not in in_range_root_ids:
            record = tree.get_record(rid)
            date = sorter._get_date_value(record)
            if sorter._is_in_date_range(date):
                in_range_root_ids.append(rid)

    total_roots = len(in_range_root_ids)
    total_records = len(in_range_records)
    processed = 0
    print(f"  共 {total_roots} 个家族树，{total_records} 条记录需要处理")

    def has_children(rid):
        return len(tree.children_map.get(rid, [])) > 0

    def batch_create_leaf_roots(leaf_root_ids):
        records_to_create = []
        old_ids_in_order = []

        for root_id in leaf_root_ids:
            record = tree.get_record(root_id)
            if record:
                records_to_create.append(
                    _prepare_fields(record, parent_field, number_fields, None)
                )
                old_ids_in_order.append(root_id)

        if not records_to_create:
            return {}, []

        id_map = {}
        if len(records_to_create) == 1:
            new_record = bitable.create_record(records_to_create[0])
            if not new_record:
                print(f"  错误：创建记录失败 (原ID: {old_ids_in_order[0]})")
                sys.exit(1)
            id_map[old_ids_in_order[0]] = new_record.get('record_id')
        else:
            result = bitable.batch_create_records(records_to_create)
            if result.get('failed', 0) > 0:
                print(f"  错误：批量创建失败 {result['failed']} 条")
                sys.exit(1)
            for i, new_rec in enumerate(result.get('records', [])):
                id_map[old_ids_in_order[i]] = new_rec.get('record_id')

        return id_map, old_ids_in_order

    pending_leaf_roots = []
    for i, root_id in enumerate(in_range_root_ids):
        if has_children(root_id):
            if pending_leaf_roots:
                print(f"\n  批量处理 {len(pending_leaf_roots)} 条无子节点的根记录...")
                id_map, to_delete = batch_create_leaf_roots(pending_leaf_roots)
                print(f"    已创建 {len(id_map)} 条记录")
                if to_delete and not bitable.batch_delete_records(to_delete):
                    print("    错误：批量删除记录失败")
                    sys.exit(1)
                if to_delete:
                    print(f"    已删除 {len(to_delete)} 条原记录")
                processed += len(pending_leaf_roots)
                pending_leaf_roots = []
                time.sleep(0.3)

            record = tree.get_record(root_id)
            fields = record.get('fields', {}) if record else {}
            date = fields.get(date_field, '-')
            family_size = len(tree.get_family_tree(root_id))

            print(f"\n  [{i + 1}/{total_roots}] 处理家族树: [{date}] (共{family_size}条)")
            print("    复制中...")
            id_map, to_delete = copy_family_tree(
                bitable, tree, root_id, parent_field, number_fields
            )
            print(f"    已复制 {len(id_map)} 条记录")

            print("    删除原记录中...")
            if to_delete and not bitable.batch_delete_records(to_delete):
                print("    错误：批量删除记录失败")
                sys.exit(1)

            print(f"    已删除 {len(to_delete)} 条原记录")
            processed += family_size
            time.sleep(0.5)
        else:
            pending_leaf_roots.append(root_id)

    if pending_leaf_roots:
        print(f"\n  批量处理 {len(pending_leaf_roots)} 条无子节点的根记录...")
        id_map, to_delete = batch_create_leaf_roots(pending_leaf_roots)
        print(f"    已创建 {len(id_map)} 条记录")
        if to_delete and not bitable.batch_delete_records(to_delete):
            print("    错误：批量删除记录失败")
            sys.exit(1)
        if to_delete:
            print(f"    已删除 {len(to_delete)} 条原记录")
        processed += len(pending_leaf_roots)

    print(f"\n  移动完成！共处理 {processed} 条记录")


def _extract_parent_id(parent_value):
    if isinstance(parent_value, dict):
        link_ids = parent_value.get('link_record_ids', [])
        return link_ids[0] if link_ids else None
    if isinstance(parent_value, list) and parent_value:
        first = parent_value[0]
        if isinstance(first, dict):
            return first.get('record_id') or first.get('id')
        if isinstance(first, str):
            return first
    if isinstance(parent_value, str):
        return parent_value
    return None


def verify_result(bitable: ReorderBitable, tree: TreeBuilder, sorter: Sorter):
    """验证最终结果。"""
    print("  重新获取表格数据...")
    new_records = bitable.get_all_records()

    old_count = len(tree.records)
    new_count = len(new_records)
    if old_count != new_count:
        print(f"  [失败] 记录总数不一致: 原 {old_count} 条，现 {new_count} 条")
    else:
        print(f"  [通过] 记录总数一致: {new_count} 条")

    parent_field = sorter.tree.parent_field
    broken_links = []
    new_id_set = {r.get('record_id') for r in new_records}

    for record in new_records:
        fields = record.get('fields', {})
        parent_id = _extract_parent_id(fields.get(parent_field))
        if parent_id and parent_id not in new_id_set:
            broken_links.append({
                'record_id': record.get('record_id'),
                'missing_parent': parent_id
            })

    if broken_links:
        print(f"  [失败] 发现 {len(broken_links)} 条父子关系断裂:")
        for item in broken_links[:10]:
            print(f"    记录 {item['record_id']} 的父记录 {item['missing_parent']} 不存在")
        if len(broken_links) > 10:
            print(f"    ... 还有 {len(broken_links) - 10} 条")
    else:
        print("  [通过] 所有父子关系完整")

    print("  [信息] 请在飞书界面确认记录物理顺序是否符合预期")


def _build_text_to_id(records: List[Dict], title_field: str) -> Dict[str, str]:
    text_to_id = {}
    if not title_field:
        return text_to_id

    for record in records:
        rid = record.get('record_id')
        title = _display_value(record.get('fields', {}).get(title_field))
        if title:
            text_to_id[str(title)] = rid
    return text_to_id


def _build_parent_map(records: List[Dict], parent_field: str,
                      title_field: str) -> Dict[str, str]:
    text_to_id = _build_text_to_id(records, title_field)
    parent_map = {}
    text_only_count = 0

    for record in records:
        rid = record.get('record_id')
        parent_value = record.get('fields', {}).get(parent_field)
        if not parent_value:
            continue

        parent_id = _extract_parent_id(parent_value)
        if not parent_id and isinstance(parent_value, list) and parent_value:
            first = parent_value[0]
            if isinstance(first, dict) and 'text' in first:
                parent_id = text_to_id.get(first.get('text'))
                if parent_id:
                    text_only_count += 1

        if parent_id:
            parent_map[rid] = parent_id

    print(f"  已识别 {len(parent_map)} 条父子关联（其中 {text_only_count} 条通过 text 反查）")
    return parent_map


def _load_reorder_config(client: FeishuClient) -> Dict:
    config = client.config.get('reorderBitable', {})
    if not config:
        print("未配置 reorderBitable，请检查 cfg/config.yaml")
        sys.exit(1)
    return config


def main():
    parser = argparse.ArgumentParser(description='飞书多维表格记录物理排序工具')
    parser.add_argument('--execute', action='store_true',
                        help='执行复制和删除；默认只预览，不写表')
    args = parser.parse_args()

    print("=" * 60)
    print("飞书多维表格记录物理排序工具")
    print("=" * 60)

    print("\n[1/5] 加载配置与授权...")
    client = FeishuClient()
    config = _load_reorder_config(client)
    target = config.get('target_table', {})
    sort_cfg = config.get('sort_config', {})
    app_token = target.get('app_token', '')
    table_id = target.get('table_id', '')
    if not app_token or not table_id:
        print("reorderBitable.target_table 缺少 app_token 或 table_id")
        sys.exit(1)
    if not client.check_token_valid() and not client.refresh_access_token():
        print("Token 刷新失败，请运行: python src/modules/feishu_auth.py")
        sys.exit(1)

    print(f"  目标表格: {table_id}")
    bitable = ReorderBitable(client, app_token, table_id)

    print("\n[2/5] 获取表格数据...")
    fields = bitable.get_table_fields()
    print(f"  已获取 {len(fields)} 个字段定义")
    number_fields = {f['field_name'] for f in fields if f.get('type') == 2}

    date_start = int(sort_cfg['date_range']['start'])
    date_end = int(sort_cfg['date_range']['end'])
    print(f"  日期范围: {date_start} ~ {date_end}")

    records = bitable.get_all_records()
    print(f"  已获取 {len(records)} 条记录")
    print("  分析父子关系...")
    parent_map = _build_parent_map(
        records, sort_cfg['parent_field'], sort_cfg.get('title_field', '')
    )

    category_options = bitable.get_field_options(fields, sort_cfg['category_field'])
    org_options = bitable.get_field_options(fields, sort_cfg['org_field'])
    priority_options = bitable.get_field_options(fields, sort_cfg['priority_field'])
    print(f"  {sort_cfg['category_field']}选项: {len(category_options)} 个")
    print(f"  {sort_cfg['org_field']}选项: {len(org_options)} 个")
    print(f"  {sort_cfg['priority_field']}选项: {len(priority_options)} 个")

    print("\n[3/5] 构建父子关系树...")
    tree = TreeBuilder(records, sort_cfg['parent_field'], parent_map)
    tree.build()
    print(f"  识别出 {len(tree.root_ids)} 个根记录")

    print("\n[4/5] 计算目标顺序...")
    sorter = Sorter(
        tree=tree,
        date_field=sort_cfg['date_field'],
        category_field=sort_cfg['category_field'],
        org_field=sort_cfg['org_field'],
        priority_field=sort_cfg['priority_field'],
        category_options=category_options,
        org_options=org_options,
        priority_options=priority_options,
        date_start=date_start,
        date_end=date_end
    )

    target_order, out_range, in_range = sorter.generate_target_order()
    current_order = sorter.get_current_order()
    need_move = print_preview(
        current_order, target_order,
        len(out_range), len(in_range), tree,
        sort_cfg['date_field'], sort_cfg['title_field'],
        sort_cfg.get('preview_count', 100),
        in_range
    )

    if not need_move:
        print("\n完成！")
        return

    if not args.execute:
        print("\n预览完成。默认不写表；如需执行，请运行: python src/reorderMain.py --execute")
        return

    print("\n[5/5] 执行记录移动...")
    execute_reorder(bitable, tree, sorter, in_range, number_fields,
                    sort_cfg['date_field'])

    print("\n[验证] 检查结果...")
    verify_result(bitable, tree, sorter)
    print("\n完成！")


if __name__ == "__main__":
    main()
