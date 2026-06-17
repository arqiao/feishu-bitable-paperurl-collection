"""
飞书多维表格记录物理排序工具。

默认只预览目标顺序；传入 --execute 后才会执行批量重排。
"""

# 运行参数说明：
#   python src/reorderMain.py
#       只读取表格、计算目标顺序并打印预览，不写入、不删除记录。
#   python src/reorderMain.py --execute
#       打印预览后批量创建新记录、批量更新父记录、批量删除旧记录。
#   python src/reorderMain.py --execute --max-temp-records 1500
#       按每批最多新增 1500 条记录分批执行，避免超过单表记录上限。
#   python src/reorderMain.py --show-records
#       预览时显示范围内目标顺序明细；默认只显示统计摘要。
#   python src/reorderMain.py --help
#       查看命令行参数帮助。

import argparse
from collections import defaultdict
import os
import sys
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
BITABLE_RECORD_LIMIT = 20000
DEFAULT_RECORD_LIMIT_MARGIN = 200


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
        records = self.client.get_raw_bitable_records(
            self.app_token,
            self.table_id,
            text_field_as_array=True,
            display_formula_ref=True,
            automatic_fields=True
        )
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

    def batch_update_records(self, records: List[Dict]) -> Dict:
        return self.client.batch_update_bitable_records(
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


def _has_nonempty_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_nonempty_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_nonempty_value(item) for item in value.values())
    return bool(value)


def audit_url_field(records: List[Dict], url_field: str, title_field: str) -> bool:
    """确认链接字段只使用真实 URL，不把显示文本当 URL。"""
    if not url_field:
        return True

    nonempty_count = 0
    extracted_count = 0
    missing_url_records = []

    for record in records:
        raw_value = record.get('fields', {}).get(url_field)
        if not _has_nonempty_value(raw_value):
            continue

        nonempty_count += 1
        if Sorter._extract_url_from_value(raw_value):
            extracted_count += 1
        else:
            missing_url_records.append(record)

    print(
        f"  链接字段审计: 非空 {nonempty_count} 条，"
        f"提取真实 URL {extracted_count} 条，"
        f"无法提取 {len(missing_url_records)} 条"
    )

    if not missing_url_records:
        return True

    print("  错误：以下记录的链接字段非空，但没有可用的 http(s) URL。")
    print("  程序不会把显示标题当作 URL 使用；请先检查这些记录的链接字段：")
    for record in missing_url_records[:10]:
        fields = record.get('fields', {})
        title = _display_value(fields.get(title_field, ''))
        raw_value = fields.get(url_field)
        print(f"    record_id={record.get('record_id')} 标题={str(title)[:80]}")
        print(f"      原始链接字段: {str(raw_value)[:300]}")
    if len(missing_url_records) > 10:
        print(f"    ... 还有 {len(missing_url_records) - 10} 条")

    return False


def print_preview(
    current_order: List[str],
    target_order: List[str],
    out_range_count: int,
    in_range_count: int,
    tree: TreeBuilder,
    date_field: str,
    title_field: str,
    preview_count: int = 100,
    in_range_ids: List[str] = None,
    show_records: bool = False
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

    if not show_records:
        print("\n【范围内目标顺序明细】默认隐藏；如需查看，请加 --show-records")
        return True

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


def _plain_text_value(value) -> str:
    if value is None:
        return ''
    if isinstance(value, dict):
        return str(
            value.get('text') or value.get('name') or
            value.get('link') or value.get('url') or ''
        )
    if isinstance(value, list):
        return ''.join(_plain_text_value(item) for item in value)
    return str(value)


def _prepare_fields(record: Dict, parent_field: str, number_fields: set,
                    text_fields: set = None, url_field: str = '') -> Dict:
    text_fields = text_fields or set()
    fields = {}
    for key, value in record.get('fields', {}).items():
        if key in SKIP_FIELDS:
            continue
        if key == url_field:
            extracted_url = Sorter._extract_url_from_value(value)
            if not extracted_url:
                continue
            value = extracted_url
        elif key in text_fields and not isinstance(value, str):
            value = _plain_text_value(value)
        if key in number_fields and isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        fields[key] = value

    fields.pop(parent_field, None)

    return fields


def _identity_value(sorter: Sorter, record: Dict, field_name: str) -> str:
    if not field_name:
        return ''
    if field_name == sorter.url_field:
        value = sorter._get_url_key(record)
        return value or ''
    value = sorter._get_field_value(record, field_name)
    if value is None:
        return ''
    return str(value).strip()


def _record_identity(sorter: Sorter, record: Dict) -> tuple:
    """Use stable business fields to match created records back to old records."""
    return (
        _identity_value(sorter, record, sorter.url_field),
        _identity_value(sorter, record, sorter.collection_field),
        _identity_value(sorter, record, sorter.title_field),
        _identity_value(sorter, record, sorter.date_field),
    )


def _has_in_range_parent_or_child(tree: TreeBuilder, record_id: str,
                                  in_range_ids: set) -> bool:
    parent_id = tree.parent_map.get(record_id)
    if parent_id is None:
        parent_id = tree._get_parent_id(tree.get_record(record_id))
    if parent_id in in_range_ids:
        return True
    return any(child_id in in_range_ids
               for child_id in tree.children_map.get(record_id, []))


def _find_single_create_ids(sorter: Sorter, tree: TreeBuilder,
                            old_ids_in_order: List[str]) -> set:
    in_range_ids = set(old_ids_in_order)
    old_buckets = defaultdict(list)
    for old_id in old_ids_in_order:
        old_buckets[_record_identity(sorter, tree.get_record(old_id))].append(old_id)

    single_create_ids = set()
    for ids in old_buckets.values():
        if len(ids) <= 1:
            continue
        if any(_has_in_range_parent_or_child(tree, rid, in_range_ids) for rid in ids):
            single_create_ids.update(ids)

    return single_create_ids


def _create_records_with_stable_mapping(
    bitable: ReorderBitable,
    sorter: Sorter,
    tree: TreeBuilder,
    old_ids_in_order: List[str],
    records_to_create: List[Dict]
) -> Dict[str, str]:
    single_create_ids = _find_single_create_ids(sorter, tree, old_ids_in_order)
    if single_create_ids:
        print(
            f"  发现 {len(single_create_ids)} 条重复身份且参与父子关系的记录，"
            "将单条创建以确保新旧 ID 映射"
        )

    id_map = {}
    pending_old_ids = []
    pending_records = []

    def flush_pending():
        if not pending_records:
            return
        result = bitable.batch_create_records(pending_records)
        if result.get('failed', 0) > 0:
            print(f"  错误：批量创建失败 {result['failed']} 条，停止执行")
            sys.exit(1)

        created_records = result.get('records', [])
        if len(created_records) != len(pending_old_ids):
            print(
                f"  错误：创建数量不一致，期望 {len(pending_old_ids)} 条，"
                f"实际 {len(created_records)} 条"
            )
            sys.exit(1)

        for old_id, created_record in zip(pending_old_ids, created_records):
            id_map[old_id] = created_record.get('record_id')
        pending_old_ids.clear()
        pending_records.clear()

    for old_id, fields in zip(old_ids_in_order, records_to_create):
        if old_id in single_create_ids:
            flush_pending()
            result = bitable.batch_create_records([fields])
            if result.get('failed', 0) > 0:
                print(f"  错误：创建重复身份记录失败 (原ID: {old_id})，停止执行")
                sys.exit(1)
            created_records = result.get('records', [])
            if len(created_records) != 1:
                print(
                    f"  错误：创建重复身份记录数量异常，原ID {old_id}，"
                    f"实际返回 {len(created_records)} 条"
                )
                sys.exit(1)
            id_map[old_id] = created_records[0].get('record_id')
        else:
            pending_old_ids.append(old_id)
            pending_records.append(fields)

    flush_pending()
    return id_map


def _split_records_by_family_batches(tree: TreeBuilder, record_ids: List[str],
                                     max_batch_records: int) -> List[List[str]]:
    if max_batch_records <= 0:
        print("  错误：每批最大新增记录数必须大于 0")
        sys.exit(1)

    record_id_set = set(record_ids)
    families = []
    seen = set()
    for record_id in record_ids:
        if record_id in seen:
            continue
        root_id = tree.get_root_record_id(record_id)
        family = [
            rid for rid in tree.get_family_tree(root_id)
            if rid in record_id_set
        ]
        if not family:
            family = [record_id]
        for rid in family:
            seen.add(rid)
        families.append(family)

    batches = []
    current_batch = []
    current_count = 0
    for family in families:
        family_size = len(family)
        if family_size > max_batch_records:
            print(
                f"  错误：家族树 {family[0]} 共 {family_size} 条，"
                f"超过每批最大新增记录数 {max_batch_records}"
            )
            sys.exit(1)
        if current_batch and current_count + family_size > max_batch_records:
            batches.append(current_batch)
            current_batch = []
            current_count = 0
        current_batch.extend(family)
        current_count += family_size

    if current_batch:
        batches.append(current_batch)
    return batches


def _resolve_max_temp_records(total_records: int,
                              requested_max_temp_records: int = None,
                              record_limit: int = BITABLE_RECORD_LIMIT,
                              safety_margin: int = DEFAULT_RECORD_LIMIT_MARGIN) -> int:
    available = record_limit - total_records - safety_margin
    if requested_max_temp_records:
        return min(requested_max_temp_records, available)
    return available


def _execute_reorder_batch(
    bitable: ReorderBitable,
    tree: TreeBuilder,
    sorter: Sorter,
    batch_records: List[str],
    number_fields: set,
    text_fields: set,
    batch_index: int,
    total_batches: int
) -> Dict:
    parent_field = sorter.tree.parent_field
    print(
        f"\n  [批次 {batch_index}/{total_batches}] "
        f"处理 {len(batch_records)} 条记录"
    )

    old_ids_in_order = []
    records_to_create = []
    for old_id in batch_records:
        record = tree.get_record(old_id)
        if not record:
            print(f"  错误：记录 {old_id} 不存在，停止执行")
            sys.exit(1)
        old_ids_in_order.append(old_id)
        records_to_create.append(
            _prepare_fields(
                record, parent_field, number_fields, text_fields, sorter.url_field
            )
        )

    print("    [1/3] 创建新记录...")
    id_map = _create_records_with_stable_mapping(
        bitable, sorter, tree, old_ids_in_order, records_to_create
    )
    if any(not new_id for new_id in id_map.values()):
        print("  错误：部分新记录缺少 record_id，停止执行")
        sys.exit(1)
    print(f"    已创建 {len(id_map)} 条新记录")

    parent_updates = []
    expected_parent_map = {}
    batch_old_ids = set(old_ids_in_order)
    for old_id in old_ids_in_order:
        parent_old_id = tree.parent_map.get(old_id)
        if parent_old_id is None:
            parent_old_id = tree._get_parent_id(tree.get_record(old_id))
        if parent_old_id and parent_old_id in batch_old_ids:
            expected_parent_map[id_map[old_id]] = id_map[parent_old_id]
            parent_updates.append({
                'record_id': id_map[old_id],
                'fields': {parent_field: [id_map[parent_old_id]]}
            })

    print("    [2/3] 更新新记录父子关系...")
    if parent_updates:
        update_result = bitable.batch_update_records(parent_updates)
        if update_result.get('failed', 0) > 0:
            print(f"  错误：批量更新父记录失败 {update_result['failed']} 条，停止执行")
            for failed in update_result.get('failed_records', [])[:10]:
                print(f"    {failed.get('record_id')}: {failed.get('error', '')}")
            sys.exit(1)
        if update_result.get('success', 0) != len(parent_updates):
            print(
                f"  错误：父记录更新数量不一致，期望 {len(parent_updates)} 条，"
                f"实际 {update_result.get('success', 0)} 条"
            )
            sys.exit(1)
    print(f"    已更新 {len(parent_updates)} 条父记录")

    print("    [3/3] 删除旧记录...")
    to_delete = list(reversed(old_ids_in_order))
    if to_delete and not bitable.batch_delete_records(to_delete):
        print("  错误：批量删除旧记录失败")
        sys.exit(1)
    print(f"    已删除 {len(to_delete)} 条旧记录")

    return {
        'old_ids': old_ids_in_order,
        'id_map': id_map,
        'expected_parent_map': expected_parent_map,
    }


def execute_reorder(bitable: ReorderBitable, tree: TreeBuilder, sorter: Sorter,
                    in_range_records: List[str], number_fields: set,
                    text_fields: set = None,
                    max_temp_records: int = None):
    """两阶段批量重排序：创建新记录，回填父记录，再删除旧记录。"""
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
    print(f"  共 {total_roots} 个家族树，{total_records} 条记录需要处理")

    resolved_max_temp_records = _resolve_max_temp_records(
        len(tree.records), max_temp_records
    )
    if resolved_max_temp_records <= 0:
        print(
            "  错误：当前表格记录数已接近或超过单表上限，"
            "没有可用于复制重排的临时新增空间"
        )
        sys.exit(1)
    print(f"  每批最多临时新增 {resolved_max_temp_records} 条记录")

    batches = _split_records_by_family_batches(
        tree, in_range_records, resolved_max_temp_records
    )
    print(f"  将按家族树切分为 {len(batches)} 个批次执行")

    all_old_ids = []
    all_id_map = {}
    all_expected_parent_map = {}
    for index, batch_records in enumerate(batches, start=1):
        batch_result = _execute_reorder_batch(
            bitable, tree, sorter, batch_records, number_fields, text_fields,
            index, len(batches)
        )
        all_old_ids.extend(batch_result['old_ids'])
        all_id_map.update(batch_result['id_map'])
        all_expected_parent_map.update(batch_result['expected_parent_map'])

    print(f"\n  移动完成！共处理 {len(all_old_ids)} 条记录")
    return {
        'old_ids': all_old_ids,
        'id_map': all_id_map,
        'expected_parent_map': all_expected_parent_map,
    }


def _extract_parent_id(parent_value):
    if isinstance(parent_value, dict):
        record_ids = parent_value.get('record_ids', [])
        if record_ids:
            return record_ids[0]
        link_ids = parent_value.get('link_record_ids', [])
        if link_ids:
            return link_ids[0]
        return parent_value.get('record_id') or parent_value.get('id')
    if isinstance(parent_value, list) and parent_value:
        first = parent_value[0]
        if isinstance(first, dict):
            record_ids = first.get('record_ids', [])
            if record_ids:
                return record_ids[0]
            link_ids = first.get('link_record_ids', [])
            if link_ids:
                return link_ids[0]
            return first.get('record_id') or first.get('id')
        if isinstance(first, str):
            return first
    if isinstance(parent_value, str):
        return parent_value
    return None


def verify_result(bitable: ReorderBitable, tree: TreeBuilder, sorter: Sorter,
                  reorder_result: Optional[Dict] = None) -> bool:
    """验证最终结果。"""
    print("  重新获取表格数据...")
    new_records = bitable.get_all_records()
    ok = True

    old_count = len(tree.records)
    new_count = len(new_records)
    if old_count != new_count:
        print(f"  [失败] 记录总数不一致: 原 {old_count} 条，现 {new_count} 条")
        ok = False
    else:
        print(f"  [通过] 记录总数一致: {new_count} 条")

    parent_field = sorter.tree.parent_field
    broken_links = []
    new_id_set = {r.get('record_id') for r in new_records}
    records_by_id = {r.get('record_id'): r for r in new_records}

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
        ok = False
    else:
        print("  [通过] 所有父子关系完整")

    if reorder_result:
        old_ids = set(reorder_result.get('old_ids', []))
        id_map = reorder_result.get('id_map', {})
        expected_parent_map = reorder_result.get('expected_parent_map', {})

        remaining_old_ids = sorted(old_ids & new_id_set)
        if remaining_old_ids:
            print(f"  [失败] 仍存在 {len(remaining_old_ids)} 条旧记录未删除:")
            for rid in remaining_old_ids[:10]:
                print(f"    {rid}")
            if len(remaining_old_ids) > 10:
                print(f"    ... 还有 {len(remaining_old_ids) - 10} 条")
            ok = False
        else:
            print("  [通过] 旧记录已全部删除")

        missing_new_ids = sorted(
            new_id for new_id in id_map.values()
            if new_id not in new_id_set
        )
        if missing_new_ids:
            print(f"  [失败] 缺少 {len(missing_new_ids)} 条新记录:")
            for rid in missing_new_ids[:10]:
                print(f"    {rid}")
            if len(missing_new_ids) > 10:
                print(f"    ... 还有 {len(missing_new_ids) - 10} 条")
            ok = False
        else:
            print("  [通过] 新记录全部存在")

        wrong_parent_links = []
        for child_id, expected_parent_id in expected_parent_map.items():
            record = records_by_id.get(child_id)
            actual_parent_id = _extract_parent_id(
                record.get('fields', {}).get(parent_field)
            ) if record else None
            if actual_parent_id != expected_parent_id:
                wrong_parent_links.append({
                    'record_id': child_id,
                    'expected_parent': expected_parent_id,
                    'actual_parent': actual_parent_id,
                })

        if wrong_parent_links:
            print(f"  [失败] 发现 {len(wrong_parent_links)} 条父记录指向错误:")
            for item in wrong_parent_links[:10]:
                print(
                    f"    记录 {item['record_id']} 期望父记录 "
                    f"{item['expected_parent']}，实际 {item['actual_parent']}"
                )
            if len(wrong_parent_links) > 10:
                print(f"    ... 还有 {len(wrong_parent_links) - 10} 条")
            ok = False
        else:
            print("  [通过] 移动记录父子关系符合预期")

    print("  [信息] 请在飞书界面确认记录物理顺序是否符合预期")
    return ok


def _build_text_to_ids(records: List[Dict], title_field: str) -> Dict[str, List[str]]:
    text_to_ids = defaultdict(list)
    if not title_field:
        return text_to_ids

    for record in records:
        rid = record.get('record_id')
        title = _display_value(record.get('fields', {}).get(title_field))
        if title:
            text_to_ids[str(title)].append(rid)
    return text_to_ids


def _field_match_value(record: Dict, field_name: str) -> str:
    if not field_name:
        return ''
    value = _display_value(record.get('fields', {}).get(field_name))
    if value is None:
        return ''
    return str(value).strip()


def _filter_parent_candidates(records_by_id: Dict[str, Dict], child_record: Dict,
                              candidates: List[str],
                              collection_field: str) -> List[str]:
    child_id = child_record.get('record_id')
    filtered = [rid for rid in candidates if rid != child_id]
    if len(filtered) <= 1 or not collection_field:
        return filtered

    child_collection = _field_match_value(child_record, collection_field)
    if not child_collection:
        return filtered

    same_collection = [
        rid for rid in filtered
        if _field_match_value(records_by_id.get(rid, {}), collection_field)
        == child_collection
    ]
    return same_collection if same_collection else filtered


def _build_parent_map(records: List[Dict], parent_field: str,
                      title_field: str,
                      collection_field: str = '') -> Dict[str, str]:
    text_to_ids = _build_text_to_ids(records, title_field)
    records_by_id = {record.get('record_id'): record for record in records}
    parent_map = {}
    text_only_count = 0
    text_lookup_errors = []

    for record in records:
        rid = record.get('record_id')
        parent_value = record.get('fields', {}).get(parent_field)
        if not parent_value:
            continue

        parent_id = _extract_parent_id(parent_value)
        if not parent_id and isinstance(parent_value, list) and parent_value:
            first = parent_value[0]
            if isinstance(first, dict) and 'text' in first:
                parent_text = str(first.get('text'))
                candidates = text_to_ids.get(parent_text, [])
                filtered = _filter_parent_candidates(
                    records_by_id, record, candidates, collection_field
                )
                if len(filtered) == 1:
                    parent_id = filtered[0]
                    text_only_count += 1
                else:
                    text_lookup_errors.append({
                        'record_id': rid,
                        'parent_text': parent_text,
                        'candidates': filtered,
                        'raw_candidates': candidates,
                        'parent_value': parent_value,
                    })

        if parent_id:
            parent_map[rid] = parent_id

    if text_lookup_errors:
        print("  错误：存在无法唯一识别的文本型父记录，停止执行。")
        print("  请确认父记录字段返回 record_id，或先消除标题重复/缺失匹配：")
        for item in text_lookup_errors[:10]:
            candidates = item['candidates'] or ['未找到匹配记录']
            print(
                f"    记录 {item['record_id']} 的父记录文本 "
                f"{item['parent_text']} 匹配到: {candidates}"
            )
            print(f"      原始父记录字段: {str(item['parent_value'])[:300]}")
        if len(text_lookup_errors) > 10:
            print(f"    ... 还有 {len(text_lookup_errors) - 10} 条")
        sys.exit(1)

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
    parser.add_argument('--max-temp-records', type=int, default=None,
                        help='每批最多临时新增记录数；默认按 20000 上限和安全余量自动计算')
    parser.add_argument('--show-records', action='store_true',
                        help='预览时显示范围内目标顺序明细；默认只显示统计摘要')
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
    text_fields = {f['field_name'] for f in fields if f.get('type') == 1}

    date_start = int(sort_cfg['date_range']['start'])
    date_end = int(sort_cfg['date_range']['end'])
    print(f"  日期范围: {date_start} ~ {date_end}")

    records = bitable.get_all_records()
    print(f"  已获取 {len(records)} 条记录")
    print("  分析父子关系...")
    parent_map = _build_parent_map(
        records,
        sort_cfg['parent_field'],
        sort_cfg.get('title_field', ''),
        sort_cfg.get('collection_field', '')
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
        date_end=date_end,
        title_field=sort_cfg.get('title_field', '标题'),
        url_field=sort_cfg.get('url_field', '链接'),
        collection_field=sort_cfg.get('collection_field', '精选合集')
    )

    target_order, out_range, in_range = sorter.generate_target_order()
    current_order = sorter.get_current_order()
    url_audit_ok = audit_url_field(
        [tree.get_record(record_id) for record_id in in_range],
        sorter.url_field,
        sorter.title_field
    )
    need_move = print_preview(
        current_order, target_order,
        len(out_range), len(in_range), tree,
        sort_cfg['date_field'], sort_cfg['title_field'],
        sort_cfg.get('preview_count', 100),
        in_range,
        args.show_records
    )

    if not need_move:
        print("\n完成！")
        return

    if not args.execute:
        print("\n预览完成。默认不写表；如需执行，请运行: python src/reorderMain.py --execute")
        return

    if not url_audit_ok:
        print("\n链接字段审计未通过，停止执行，避免把显示文本当成 URL 或丢失链接。")
        sys.exit(1)

    print("\n[5/5] 执行记录移动...")
    reorder_result = execute_reorder(
        bitable, tree, sorter, in_range, number_fields, text_fields,
        args.max_temp_records
    )

    print("\n[验证] 检查结果...")
    verify_result(bitable, tree, sorter, reorder_result)
    print("\n完成！")


if __name__ == "__main__":
    main()
