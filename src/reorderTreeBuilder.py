"""
父子关系树构建模块。
"""

from typing import Dict, List, Optional, Set


class TreeBuilder:
    """构建父子关系树。"""

    def __init__(self, records: List[Dict], parent_field: str,
                 parent_map: Optional[Dict[str, str]] = None):
        self.records = records
        self.parent_field = parent_field
        self.parent_map = parent_map or {}
        self.record_map: Dict[str, Dict] = {}
        self.children_map: Dict[str, List[str]] = {}
        self.root_ids: List[str] = []

    def build(self) -> None:
        """构建关系树。"""
        for record in self.records:
            record_id = record.get('record_id')
            self.record_map[record_id] = record
            self.children_map[record_id] = []

        for record in self.records:
            record_id = record.get('record_id')
            parent_id = self.parent_map.get(record_id)
            if parent_id is None:
                parent_id = self._get_parent_id(record)

            if parent_id is None:
                self.root_ids.append(record_id)
            elif parent_id not in self.record_map:
                self.root_ids.append(record_id)
            else:
                self.children_map[parent_id].append(record_id)

        self._handle_cycles()

    def _get_parent_id(self, record: Dict) -> Optional[str]:
        """从记录中提取父记录 ID。"""
        fields = record.get('fields', {})
        parent_value = fields.get(self.parent_field)

        if not parent_value:
            return None

        if isinstance(parent_value, dict):
            link_ids = parent_value.get('link_record_ids', [])
            if link_ids:
                return link_ids[0]
            return None

        if isinstance(parent_value, list) and len(parent_value) > 0:
            first_link = parent_value[0]
            if isinstance(first_link, dict):
                return first_link.get('record_id')
            return first_link

        if isinstance(parent_value, str):
            return parent_value

        return None

    def _handle_cycles(self) -> None:
        """检测并处理循环引用。"""
        visited: Set[str] = set()
        in_stack: Set[str] = set()

        def dfs(record_id: str) -> bool:
            if record_id in in_stack:
                return True
            if record_id in visited:
                return False

            visited.add(record_id)
            in_stack.add(record_id)

            for child_id in list(self.children_map.get(record_id, [])):
                if dfs(child_id):
                    self.children_map[record_id].remove(child_id)
                    if child_id not in self.root_ids:
                        self.root_ids.append(child_id)

            in_stack.remove(record_id)
            return False

        for record_id in list(self.record_map):
            dfs(record_id)

    def get_root_record_id(self, record_id: str) -> str:
        """获取某记录的根父记录 ID。"""
        visited = set()
        current = record_id

        while current:
            if current in visited:
                return current
            visited.add(current)

            parent_id = self.parent_map.get(current)
            if parent_id is None:
                record = self.record_map.get(current)
                if record:
                    parent_id = self._get_parent_id(record)
            if parent_id is None or parent_id not in self.record_map:
                return current
            current = parent_id

        return record_id

    def get_family_tree(self, root_id: str) -> List[str]:
        """获取以 root_id 为根的整个家族树，子孙保持原有顺序。"""
        result = [root_id]
        self._collect_descendants(root_id, result)
        return result

    def _collect_descendants(self, record_id: str, result: List[str]) -> None:
        for child_id in self.children_map.get(record_id, []):
            result.append(child_id)
            self._collect_descendants(child_id, result)

    def get_record(self, record_id: str) -> Optional[Dict]:
        """获取记录。"""
        return self.record_map.get(record_id)
