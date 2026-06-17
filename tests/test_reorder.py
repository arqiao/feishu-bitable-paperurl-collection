import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from reorderSorter import Sorter
from reorderTreeBuilder import TreeBuilder


def make_record(record_id, date='20260101', parent=None,
                category='A', org='Org1', priority='P1'):
    fields = {
        '日期': date,
        '标题': record_id,
        '主题分类': category,
        '企业组织': org,
        '兴趣优先级': priority,
    }
    if parent:
        fields['父记录'] = [{'record_id': parent}]
    return {'record_id': record_id, 'fields': fields}


class ReorderTests(unittest.TestCase):
    def test_tree_builder_handles_family_tree_and_broken_parent(self):
        records = [
            make_record('root'),
            make_record('child', parent='root'),
            make_record('grandchild', parent='child'),
            make_record('broken', parent='missing'),
        ]

        tree = TreeBuilder(records, '父记录')
        tree.build()

        self.assertEqual(tree.root_ids, ['root', 'broken'])
        self.assertEqual(tree.children_map['root'], ['child'])
        self.assertEqual(tree.children_map['child'], ['grandchild'])
        self.assertEqual(tree.get_family_tree('root'), ['root', 'child', 'grandchild'])

    def test_tree_builder_breaks_cycle_without_natural_root(self):
        records = [
            make_record('a', parent='b'),
            make_record('b', parent='a'),
        ]

        tree = TreeBuilder(records, '父记录')
        tree.build()

        self.assertTrue(tree.root_ids)
        family = tree.get_family_tree(tree.root_ids[0])
        self.assertEqual(sorted(family), ['a', 'b'])

    def test_sorter_keeps_out_of_range_first_and_sorts_in_range_roots(self):
        records = [
            make_record('out', date='20251231'),
            make_record('late_z', date='20260102', category='Z'),
            make_record('early', date='20260101'),
            make_record('early_child', date='20260101', parent='early'),
            make_record('late_a', date='20260102', category='A'),
            make_record('late_empty', date='20260102', category=''),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A', 'Z'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
        )

        target_order, out_range, in_range = sorter.generate_target_order()

        self.assertEqual(out_range, ['out'])
        self.assertEqual(in_range, ['early', 'early_child', 'late_a', 'late_z', 'late_empty'])
        self.assertEqual(target_order, out_range + in_range)


if __name__ == '__main__':
    unittest.main()
