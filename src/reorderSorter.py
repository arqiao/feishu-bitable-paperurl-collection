"""
多维表格物理重排的排序逻辑。
"""

import re
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
        date_end: int,
        title_field: str = '',
        url_field: str = '',
        collection_field: str = ''
    ):
        self.tree = tree
        self.date_field = date_field
        self.title_field = title_field
        self.category_field = category_field
        self.org_field = org_field
        self.priority_field = priority_field
        self.url_field = url_field
        self.collection_field = collection_field
        self.category_order = {v: i for i, v in enumerate(category_options)}
        self.org_order = {v: i for i, v in enumerate(org_options)}
        self.priority_order = {v: i for i, v in enumerate(priority_options)}
        self.date_start = date_start
        self.date_end = date_end

    @staticmethod
    def _is_url_string(value: str) -> bool:
        return bool(re.match(r'^https?://', str(value).strip()))

    @classmethod
    def _extract_url_from_value(cls, value) -> Optional[str]:
        """从飞书结构化字段中提取真实 URL，不能用显示文本兜底。"""
        if value is None:
            return None

        if isinstance(value, str):
            value = value.strip()
            return value if cls._is_url_string(value) else None

        if isinstance(value, dict):
            for key in ('link', 'url', 'href'):
                candidate = value.get(key)
                if isinstance(candidate, str) and cls._is_url_string(candidate):
                    return candidate.strip()
                if isinstance(candidate, dict):
                    nested = cls._extract_url_from_value(candidate)
                    if nested:
                        return nested

            for nested_value in value.values():
                nested = cls._extract_url_from_value(nested_value)
                if nested:
                    return nested
            return None

        if isinstance(value, list):
            for item in value:
                nested = cls._extract_url_from_value(item)
                if nested:
                    return nested
            return None

        return None

    def _get_field_value(self, record: Dict, field_name: str) -> Optional[str]:
        """获取字段值。"""
        fields = record.get('fields', {})
        value = fields.get(field_name)

        if value is None:
            return None

        if isinstance(value, dict):
            return value.get('link') or value.get('url') or value.get('text') or value.get('name')
        if isinstance(value, list) and len(value) > 0:
            first = value[0]
            if isinstance(first, dict):
                return first.get('link') or first.get('url') or first.get('text') or first.get('name')
            return str(first)

        return str(value) if value else None

    def _get_url_key(self, record: Dict) -> Optional[str]:
        """获取用于 URL 分组的稳定键。"""
        if not self.url_field:
            return None
        value = self._extract_url_from_value(
            record.get('fields', {}).get(self.url_field)
        )
        if not value:
            return None
        return str(value).strip()

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

    def _build_url_group_keys(self, root_ids: List[str]) -> Dict[str, Tuple[Tuple, int]]:
        """同 URL 根节点使用首次出现记录的排序键作为整组排序依据。"""
        group_keys = {}
        for index, root_id in enumerate(root_ids):
            record = self.tree.get_record(root_id)
            if not record:
                continue
            url_key = self._get_url_key(record)
            if url_key and url_key not in group_keys:
                group_keys[url_key] = (self._get_sort_key(root_id), index)
        return group_keys

    def _get_grouped_sort_key(
        self,
        root_id: str,
        group_keys: Dict[str, Tuple[Tuple, int]],
        original_index: int
    ) -> Tuple:
        record = self.tree.get_record(root_id)
        if not record:
            return ((float('inf'),) * 4, 1, original_index, '', original_index)

        url_key = self._get_url_key(record)
        if url_key and url_key in group_keys:
            group_sort_key, first_index = group_keys[url_key]
            collection = ''
            if self.collection_field:
                collection = self._get_field_value(record, self.collection_field) or ''
            return (group_sort_key, 0, first_index, url_key, str(collection), original_index)

        return (self._get_sort_key(root_id), 1, original_index, '', '', original_index)

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

        group_keys = self._build_url_group_keys(in_range_roots)
        root_index = {root_id: i for i, root_id in enumerate(in_range_roots)}
        in_range_roots.sort(
            key=lambda root_id: self._get_grouped_sort_key(
                root_id, group_keys, root_index[root_id]
            )
        )

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
