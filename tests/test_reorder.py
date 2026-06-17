import contextlib
import io
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from reorderMain import (
    execute_reorder,
    verify_result,
    audit_url_field,
    _build_parent_map,
    _prepare_fields,
    _record_identity,
)
from reorderSorter import Sorter
from reorderTreeBuilder import TreeBuilder


def make_record(record_id, date='20260101', parent=None,
                category='A', org='Org1', priority='P1',
                url=None, collection=None, title=None):
    fields = {
        '日期': date,
        '标题': title or record_id,
        '主题分类': category,
        '企业组织': org,
        '兴趣优先级': priority,
    }
    if url:
        fields['链接'] = url
    if collection:
        fields['精选合集'] = collection
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
            title_field='标题',
        )

        target_order, out_range, in_range = sorter.generate_target_order()

        self.assertEqual(out_range, ['out'])
        self.assertEqual(in_range, ['early', 'early_child', 'late_a', 'late_z', 'late_empty'])
        self.assertEqual(target_order, out_range + in_range)

    def test_sorter_groups_same_url_roots_by_first_seen_category(self):
        records = [
            make_record('url_first_b', category='B', url='https://example.com/a', collection='合集B'),
            make_record('other_a', category='A', url='https://example.com/other', collection='合集A'),
            make_record('url_later_a', category='A', url='https://example.com/a', collection='合集C'),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A', 'B'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            url_field='链接',
            collection_field='精选合集',
        )

        _, _, in_range = sorter.generate_target_order()

        self.assertEqual(in_range, ['other_a', 'url_first_b', 'url_later_a'])

    def test_sorter_url_key_uses_real_url_not_display_text(self):
        records = [
            make_record('r1'),
            make_record('r2'),
        ]
        records[0]['fields']['链接'] = [{
            'type': 'text',
            'text': '显示标题',
            'link': {'url': 'https://example.com/real'},
        }]
        records[1]['fields']['链接'] = [{
            'type': 'text',
            'text': '只是显示标题',
        }]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            title_field='标题',
            url_field='链接',
            collection_field='精选合集',
        )

        self.assertEqual(sorter._get_url_key(records[0]), 'https://example.com/real')
        self.assertIsNone(sorter._get_url_key(records[1]))
        self.assertEqual(_record_identity(sorter, records[1])[0], '')

    def test_audit_url_field_rejects_nonempty_link_without_real_url(self):
        records = [
            {
                'record_id': 'bad',
                'fields': {
                    '标题': 'bad',
                    '链接': [{'type': 'text', 'text': '显示标题'}],
                }
            },
            {
                'record_id': 'good',
                'fields': {
                    '标题': 'good',
                    '链接': [{'type': 'text', 'link': {'url': 'https://example.com'}}],
                }
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()) as output:
            ok = audit_url_field(records, '链接', '标题')

        self.assertFalse(ok)
        self.assertIn('无法提取 1 条', output.getvalue())

    def test_sorter_does_not_group_same_url_child_records_globally(self):
        records = [
            make_record('root_b', category='B'),
            make_record('child_b', parent='root_b', url='https://example.com/child'),
            make_record('root_a', category='A'),
            make_record('child_a', parent='root_a', url='https://example.com/child'),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A', 'B'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            url_field='链接',
            collection_field='精选合集',
        )

        _, _, in_range = sorter.generate_target_order()

        self.assertEqual(in_range, ['root_a', 'child_a', 'root_b', 'child_b'])

    def test_execute_reorder_uses_two_stage_batch_updates(self):
        records = [
            make_record('root', date='20260102'),
            make_record('child', date='20260102', parent='root'),
            make_record('other', date='20260101'),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            title_field='标题',
        )
        _, _, in_range = sorter.generate_target_order()
        fake_bitable = FakeBitable()

        with contextlib.redirect_stdout(io.StringIO()):
            execute_reorder(fake_bitable, tree, sorter, in_range, set())

        self.assertEqual(fake_bitable.created_old_titles, ['other', 'root', 'child'])
        self.assertNotIn('父记录', fake_bitable.created_fields[2])
        self.assertEqual(
            fake_bitable.updated_records,
            [{'record_id': 'new_child', 'fields': {'父记录': ['new_root']}}]
        )
        self.assertEqual(fake_bitable.deleted_ids, ['child', 'root', 'other'])

    def test_execute_reorder_maps_created_records_by_create_response_order(self):
        records = [
            make_record('a', url='https://mp.weixin.qq.com/s/root', collection='合集1'),
            make_record('b1', parent='a', url='https://waytoagi.feishu.cn/wiki/child1', collection='合集1'),
            make_record('b2', parent='a', url='https://blank-page-236.youware.app', collection='合集1'),
            make_record('b3', parent='a', url='https://mp.weixin.qq.com/s?__biz=child3', collection='合集1'),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            title_field='标题',
            url_field='链接',
            collection_field='精选合集',
        )
        _, _, in_range = sorter.generate_target_order()
        fake_bitable = FakeBitable(omit_created_fields=True)

        with contextlib.redirect_stdout(io.StringIO()):
            execute_reorder(fake_bitable, tree, sorter, in_range, set())

        self.assertEqual(
            fake_bitable.updated_records,
            [
                {'record_id': 'new_b1', 'fields': {'父记录': ['new_a']}},
                {'record_id': 'new_b2', 'fields': {'父记录': ['new_a']}},
                {'record_id': 'new_b3', 'fields': {'父记录': ['new_a']}},
            ]
        )

    def test_execute_reorder_single_creates_ambiguous_duplicate_identity_records(self):
        records = [
            make_record(
                'p1', title='dup', url='https://example.com/dup',
                collection='合集'
            ),
            make_record('child', parent='p1'),
            make_record(
                'p2', title='dup', url='https://example.com/dup',
                collection='合集'
            ),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            title_field='标题',
            url_field='链接',
            collection_field='精选合集',
        )
        _, _, in_range = sorter.generate_target_order()
        fake_bitable = FakeBitable()

        with contextlib.redirect_stdout(io.StringIO()):
            execute_reorder(fake_bitable, tree, sorter, in_range, set())

        self.assertEqual(fake_bitable.created_old_titles, ['dup', 'child', 'dup'])
        self.assertEqual(fake_bitable.create_batch_sizes, [1, 1, 1])
        self.assertEqual(
            fake_bitable.updated_records,
            [{'record_id': 'new_child', 'fields': {'父记录': ['new_dup']}}]
        )

    def test_execute_reorder_preserves_parents_for_duplicate_child_identity(self):
        records = [
            make_record('p1'),
            make_record(
                'c1', parent='p1', title='dup_child',
                url='https://example.com/child', collection='合集'
            ),
            make_record('p2'),
            make_record(
                'c2', parent='p2', title='dup_child',
                url='https://example.com/child', collection='合集'
            ),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            title_field='标题',
            url_field='链接',
            collection_field='精选合集',
        )
        _, _, in_range = sorter.generate_target_order()
        fake_bitable = FakeBitable()

        with contextlib.redirect_stdout(io.StringIO()):
            execute_reorder(fake_bitable, tree, sorter, in_range, set())

        self.assertEqual(
            fake_bitable.updated_records,
            [
                {'record_id': 'new_dup_child', 'fields': {'父记录': ['new_p1']}},
                {'record_id': 'new_dup_child_2', 'fields': {'父记录': ['new_p2']}},
            ]
        )

    def test_prepare_fields_normalizes_structured_text_values(self):
        record = {
            'record_id': 'rec1',
            'fields': {
                '标题': [
                    {'type': 'text', 'text': 'Hello'},
                    {'type': 'text', 'text': ' World'},
                ],
                '父记录': [{'record_ids': ['parent'], 'text': 'parent'}],
            }
        }

        fields = _prepare_fields(record, '父记录', set(), {'标题'})

        self.assertEqual(fields['标题'], 'Hello World')
        self.assertNotIn('父记录', fields)

    def test_prepare_fields_preserves_real_url_from_structured_link_field(self):
        record = {
            'record_id': 'rec1',
            'fields': {
                '链接': [{
                    'type': 'text',
                    'text': '显示标题',
                    'link': {'url': 'https://example.com/real'},
                }],
            }
        }

        fields = _prepare_fields(record, '父记录', set(), {'链接'}, '链接')

        self.assertEqual(fields['链接'], 'https://example.com/real')

    def test_prepare_fields_skips_link_field_without_real_url(self):
        record = {
            'record_id': 'rec1',
            'fields': {
                '链接': [{
                    'type': 'text',
                    'text': '2.1.5 AR/VR/元宇宙/虚拟人（110篇）',
                }],
            }
        }

        fields = _prepare_fields(record, '父记录', set(), {'链接'}, '链接')

        self.assertNotIn('链接', fields)

    def test_build_parent_map_rejects_ambiguous_text_parent(self):
        records = [
            {'record_id': 'a1', 'fields': {'标题': '重复标题'}},
            {'record_id': 'a2', 'fields': {'标题': '重复标题'}},
            {
                'record_id': 'child',
                'fields': {
                    '标题': 'child',
                    '父记录': [{'text': '重复标题'}],
                }
            },
        ]

        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                _build_parent_map(records, '父记录', '标题')

    def test_build_parent_map_prefers_link_record_ids_over_text(self):
        records = [
            {'record_id': 'a1', 'fields': {'标题': '重复标题'}},
            {'record_id': 'a2', 'fields': {'标题': '重复标题'}},
            {
                'record_id': 'child',
                'fields': {
                    '标题': 'child',
                    '父记录': [{
                        'text': '重复标题',
                        'link_record_ids': ['a1'],
                    }],
                }
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            parent_map = _build_parent_map(records, '父记录', '标题')

        self.assertEqual(parent_map['child'], 'a1')

    def test_build_parent_map_prefers_record_ids_over_text(self):
        records = [
            {'record_id': 'a1', 'fields': {'标题': '重复标题'}},
            {'record_id': 'a2', 'fields': {'标题': '重复标题'}},
            {
                'record_id': 'child',
                'fields': {
                    '标题': 'child',
                    '父记录': [{
                        'record_ids': ['a1'],
                        'table_id': 'tbl_test',
                        'text': '重复标题',
                        'text_arr': ['重复标题'],
                        'type': 'text',
                    }],
                }
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            parent_map = _build_parent_map(records, '父记录', '标题')

        self.assertEqual(parent_map['child'], 'a1')

    def test_build_parent_map_text_lookup_excludes_self_candidate(self):
        records = [
            {'record_id': 'parent', 'fields': {'标题': '重复标题'}},
            {
                'record_id': 'child',
                'fields': {
                    '标题': '重复标题',
                    '父记录': [{'text': '重复标题'}],
                }
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            parent_map = _build_parent_map(records, '父记录', '标题')

        self.assertEqual(parent_map['child'], 'parent')

    def test_build_parent_map_text_lookup_uses_collection_when_needed(self):
        records = [
            {'record_id': 'parent_a', 'fields': {'标题': '重复标题', '精选合集': 'A'}},
            {'record_id': 'parent_b', 'fields': {'标题': '重复标题', '精选合集': 'B'}},
            {
                'record_id': 'child',
                'fields': {
                    '标题': 'child',
                    '精选合集': 'B',
                    '父记录': [{'text': '重复标题'}],
                }
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            parent_map = _build_parent_map(records, '父记录', '标题', '精选合集')

        self.assertEqual(parent_map['child'], 'parent_b')

    def test_verify_result_detects_wrong_recreated_parent(self):
        records = [
            make_record('a'),
            make_record('b1', parent='a'),
            make_record('b2', parent='a'),
        ]
        tree = TreeBuilder(records, '父记录')
        tree.build()
        sorter = Sorter(
            tree=tree,
            date_field='日期',
            category_field='主题分类',
            org_field='企业组织',
            priority_field='兴趣优先级',
            category_options=['A'],
            org_options=['Org1'],
            priority_options=['P1'],
            date_start=20260101,
            date_end=20260110,
            title_field='标题',
        )
        fake_bitable = FakeBitable(records_after=[
            make_record('new_a'),
            make_record('new_b1', parent='new_a'),
            make_record('new_b2', parent='new_b1'),
        ])
        reorder_result = {
            'old_ids': ['a', 'b1', 'b2'],
            'id_map': {
                'a': 'new_a',
                'b1': 'new_b1',
                'b2': 'new_b2',
            },
            'expected_parent_map': {
                'new_b1': 'new_a',
                'new_b2': 'new_a',
            },
        }

        with contextlib.redirect_stdout(io.StringIO()) as output:
            ok = verify_result(fake_bitable, tree, sorter, reorder_result)

        self.assertFalse(ok)
        self.assertIn('父记录指向错误', output.getvalue())


class FakeBitable:
    def __init__(self, omit_created_fields=False, records_after=None):
        self.created_fields = []
        self.created_old_titles = []
        self.updated_records = []
        self.deleted_ids = []
        self.omit_created_fields = omit_created_fields
        self.records_after = records_after or []
        self.create_batch_sizes = []
        self.title_counts = {}

    def batch_create_records(self, records):
        self.create_batch_sizes.append(len(records))
        self.created_fields.extend(dict(r) for r in records)
        self.created_old_titles.extend([r['标题'] for r in records])
        created = []
        for fields in records:
            title = fields['标题']
            self.title_counts[title] = self.title_counts.get(title, 0) + 1
            suffix = '' if self.title_counts[title] == 1 else f"_{self.title_counts[title]}"
            created.append({
                'record_id': f"new_{title}{suffix}",
                'fields': {} if self.omit_created_fields else dict(fields)
            })
        return {
            'success': len(records),
            'failed': 0,
            'records': created
        }

    def batch_update_records(self, records):
        self.updated_records = records
        return {'success': len(records), 'failed': 0, 'failed_records': []}

    def batch_delete_records(self, record_ids):
        self.deleted_ids = record_ids
        return True

    def get_all_records(self):
        return self.records_after


if __name__ == '__main__':
    unittest.main()
