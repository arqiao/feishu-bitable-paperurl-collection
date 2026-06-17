"""
飞书多维表格 URL 本地缓存模块

避免每次运行都全量拉取多维表格记录。
缓存文件按 table_id 分文件存储在 data/ 目录下。

用法：
    cache = BitableUrlCache(table_id, data_dir)
    url_set, url_to_rid = cache.load()
    if cache.is_empty():
        cache.rebuild(client, app_token, table_id)
        url_set, url_to_rid = cache.load()
    # 写入多维表格后追加
    cache.append([{'url': '...', 'record_id': 'rec...'}])
"""

import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from url_parser import normalize_url


class BitableUrlCache:
    """飞书多维表格 URL 本地缓存

    缓存文件格式（CSV，UTF-8 BOM）：
        url, record_id, added_at
    """

    HEADERS = ['url', 'record_id', 'added_at']

    def __init__(self, table_id: str, data_dir: str):
        """
        Args:
            table_id: 飞书多维表格 table_id，用于区分缓存文件
            data_dir: 缓存文件存放目录（通常为项目 data/）
        """
        self._file = os.path.join(
            data_dir, f'bitable_url_cache_{table_id}.csv')
        self._loaded = False
        self._url_set: set = set()
        self._url_to_rid: dict = {}

    # ── 公开接口 ──────────────────────────────────────────────

    def load(self) -> tuple:
        """从本地缓存文件加载 URL 集合

        Returns:
            (url_set, url_to_record_id)
            url_set: set[str] — 所有已有 URL
            url_to_record_id: dict[str, str] — URL → 飞书 record_id
        """
        self._url_set = set()
        self._url_to_rid = {}
        if not os.path.exists(self._file):
            self._loaded = True
            return self._url_set, self._url_to_rid

        with open(self._file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get('url', '').strip()
                rid = row.get('record_id', '').strip()
                if url:
                    norm = normalize_url(url)
                    self._url_set.add(norm)
                    if rid:
                        self._url_to_rid[norm] = rid

        self._loaded = True
        return self._url_set, self._url_to_rid

    def is_empty(self) -> bool:
        """缓存文件不存在或内容为空"""
        if not self._loaded:
            self.load()
        return len(self._url_set) == 0

    def rebuild(self, client, app_token: str, table_id: str,
                url_field: str = '链接') -> int:
        """全量从多维表格重建本地缓存（覆盖写）

        Args:
            client: FeishuClient 实例
            app_token: 多维表格 app_token
            table_id: 多维表格 table_id
            url_field: URL 字段名，默认"链接"

        Returns:
            int: 写入缓存的记录数
        """
        print(f'  从多维表格全量重建缓存 (table={table_id})...', flush=True)
        records = client.get_bitable_records(
            app_token, table_id, [url_field])

        if not records and getattr(client, 'last_error', None):
            print(f'  缓存重建失败：{client.last_error}', flush=True)
            return 0

        entries = []
        for rec in records:
            val = rec.get(url_field, '')
            if isinstance(val, dict):
                val = val.get('link', '') or val.get('text', '')
            url = str(val).strip() if val else ''
            if not url:
                continue
            rid = rec.get('_record_id', '')
            entries.append({'url': url, 'record_id': rid})

        self._write_all(entries)
        # 重置内存状态，下次 load() 重新读
        self._loaded = False
        print(f'  缓存重建完成: {len(entries)} 条', flush=True)
        return len(entries)

    def append(self, entries: list):
        """增量追加新记录到缓存文件

        Args:
            entries: [{'url': str, 'record_id': str}, ...]
        """
        if not entries:
            return
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        need_header = (not os.path.exists(self._file) or
                       os.path.getsize(self._file) == 0)
        with open(self._file, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if need_header:
                writer.writerow(self.HEADERS)
            for e in entries:
                url = e.get('url', '').strip()
                rid = e.get('record_id', '')
                if url:
                    norm = normalize_url(url)
                    writer.writerow([url, rid, now_str])
                    # 同步更新内存（用归一化后的 URL 做 key）
                    self._url_set.add(norm)
                    if rid:
                        self._url_to_rid[norm] = rid

    # ── 内部方法 ──────────────────────────────────────────────

    def _write_all(self, entries: list):
        """覆盖写全部记录到缓存文件"""
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self._file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(self.HEADERS)
            for e in entries:
                url = e.get('url', '').strip()
                rid = e.get('record_id', '')
                if url:
                    writer.writerow([url, rid, now_str])
