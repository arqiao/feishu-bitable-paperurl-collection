"""
多维表格物理重排的排序逻辑。
"""

from typing import Dict, List, Optional, Tuple

from reorderTreeBuilder import TreeBuilder


class Sorter:
    """记录排序器。"""

    def __init__(
        self,
        tree: TreeBuilder,
        date_field: str,
        category_field: str,
        org_field: str,
        priority_field: str,
        category_options: List[str],
        org_options: List[str],
        priority_options: List[str],
        date_start: int,
        date_end: int
    ):
        self.tree = tree
        self.date_field = date_field
        self.category_field = category_field
        self.org_field = org_field
        self.priority_field = priority_field
        self.category_order = {v: i for i, v in enumerate(category_options)}
        self.org_order = {v: i for i, v in enumerate(org_options)}
        self.priority_order = {v: i for i, v in enumerate(priority_options)}
        self.date_start = date_start
        self.date_end = date_end

    def _get_field_value(self, record: Dict, field_name: str) -> Optional[str]:
        """获取字段值。"""
        fields = record.get('fields', {})
        value = fields.get(field_name)

        if value is None:
            return None

        if isinstance(value, dict):
            return value.get('text') or value.get('name')
        if isinstance(value, list) and len(value) > 0:
            first = value[0]
            if isinstance(first, dict):
                return first.get('text') or first.get('name')
            return str(first)

        return str(value) if value else None

    def _get_date_value(self, record: Dict) -> Optional[int]:
        """获取日期字段值。"""
        value = self._get_field_value(record, self.date_field)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _is_in_date_range(self, date_value: Optional[int]) -> bool:
        """判断日期是否在范围内。"""
        if date_value is None:
            return False
        return self.date_start <= date_value <= self.date_end

    def _get_sort_key(self, root_id: str) -> Tuple:
        """计算根记录的排序键。"""
        record = self.tree.get_record(root_id)
        if not record:
            return (float('inf'),) * 4

        date = self._get_date_value(record)
        category = self._get_field_value(record, self.category_field)
        org = self._get_field_value(record, self.org_field)
        priority = self._get_field_value(record, self.priority_field)

        max_order = 999999
        date_key = date if date else float('inf')
        category_key = self.category_order.get(category, max_order)
        org_key = self.org_order.get(org, max_order)
        priority_key = self.priority_order.get(priority, max_order)

        return (date_key, category_key, org_key, priority_key)

    def generate_target_order(self) -> Tuple[List[str], List[str], List[str]]:
        """
        生成目标顺序。

        返回: (目标顺序列表, 范围外记录列表, 范围内记录列表)
        """
        in_range_roots = []
        out_range_roots = []

        for root_id in self.tree.root_ids:
            record = self.tree.get_record(root_id)
            date = self._get_date_value(record)

            if self._is_in_date_range(date):
                in_range_roots.append(root_id)
            else:
                out_range_roots.append(root_id)

        in_range_roots.sort(key=self._get_sort_key)

        out_range_records = []
        for root_id in out_range_roots:
            out_range_records.extend(self.tree.get_family_tree(root_id))

        in_range_records = []
        for root_id in in_range_roots:
            in_range_records.extend(self.tree.get_family_tree(root_id))

        return out_range_records + in_range_records, out_range_records, in_range_records

    def get_current_order(self) -> List[str]:
        """获取当前记录顺序。"""
        return [r.get('record_id') for r in self.tree.records]
