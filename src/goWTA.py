"""
WaytoAGI 飞书知识库 → 飞书多维表格
从 WaytoAGI 飞书知识库文档中按日期范围提取 URL，解析文章信息，写入飞书多维表格。

用法:
  python src/goWTA.py --his 20260301 20260307  # 历史批量处理
  python src/goWTA.py --update                 # 增量更新（基于 last_processed_date）

参数:
  --his START END    历史批量处理，指定日期范围（YYYYMMDD）
  --update           增量更新，从 last_processed_date 到今天

输出文件:
  log-err/wta_urls_YYYYMMDD.csv      提取的 URL 列表
  log-err/wta_parsed_YYYYMMDD.csv    解析结果
  log-err/wta_errors_YYYYMMDD.csv    解析失败日志
  data/bitable_cache_*.csv           多维表格 URL 缓存
"""

import csv
import io
import os
import re
import sys
import time
import argparse
from datetime import datetime, timedelta

# Windows UTF-8 输出
def _setup_encoding():
    if sys.platform == 'win32':
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                          line_buffering=True)
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8',
                                          line_buffering=True)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from feishu_client import FeishuClient
from url_parser import UrlParser, normalize_url
from goAIPM import get_tenant_headers, get_doc_blocks
from modules.config_utils import set_config_value_preserve_comments
from modules.bitable_url_cache import BitableUrlCache

LOG_ERR_DIR = os.path.join(PROJECT_ROOT, 'log-err')
os.makedirs(LOG_ERR_DIR, exist_ok=True)

# 飞书 wiki 自身域名（用于过滤子页面内部链接）
FEISHU_WIKI_DOMAINS = ['waytoagi.feishu.cn', 'feishu.cn/wiki', 'feishu.cn/docx']

# 重试无意义的域名（结构性失败，非偶发网络问题）
SKIP_RETRY_DOMAINS = ['github.com', 'huggingface.co', '127.0.0.1', 'localhost']

# CSV 表头
PARSED_HEADERS = ['链接', '标题', '日期', '星期', '来源', '精选日期', '异常信息']

WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

# 本地 URL 缓存文件
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
PROCESSED_URLS_FILE = None  # 在 main() 中根据 table_id 动态设置


def calc_weekday(date_str):
    """根据 YYYYMMDD 日期字符串计算星期"""
    try:
        dt = datetime.strptime(date_str, '%Y%m%d')
        return WEEKDAY_NAMES[dt.weekday()]
    except (ValueError, TypeError):
        return ''


def load_processed_urls(start_date='', end_date=''):
    """从本地缓存加载已处理 URL

    Args:
        start_date, end_date: 精选日期范围，dedup_keys 只加入范围内的记录

    Returns:
        (url_to_rid, max_id, url_to_cache_id, dedup_keys,
         parent_children, cache_id_to_norm, norm_to_parent_id)
        url_to_rid: {normalized_url: record_id} — 父记录查找（全量）
        max_id: int — 当前最大 ID（全量）
        url_to_cache_id: {normalized_url: cache_id} — 父子关联用（全量）
        dedup_keys: {(normalized_url, layer, parent_id)} — 仅范围内
        parent_children: {parent_cache_id: [(norm_url, layer, cache_id), ...]} — 全量
        cache_id_to_norm: {cache_id: normalized_url} — 反查用（全量）
        norm_to_parent_id: {normalized_url: parent_cache_id} — 查父节点（全量）
    """
    url_to_rid = {}
    url_to_cache_id = {}
    dedup_keys = set()
    parent_children = {}
    cache_id_to_norm = {}
    norm_to_parent_id = {}
    max_id = 0
    if not os.path.exists(PROCESSED_URLS_FILE):
        return (url_to_rid, max_id, url_to_cache_id, dedup_keys,
                parent_children, cache_id_to_norm, norm_to_parent_id)
    with open(PROCESSED_URLS_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get('url', '').strip()
            rid = row.get('record_id', '').strip()
            cache_id = row.get('id', '').strip()
            layer = row.get('layer', '').strip()
            parent_id = row.get('parent_id', '').strip()
            section = row.get('精选日期', '').strip()
            if url:
                norm = normalize_url(url)
                url_to_rid[norm] = rid
                # dedup_keys 只加入精选日期在范围内的记录
                if section and start_date <= section <= end_date:
                    dedup_keys.add((norm, layer, parent_id))
                if cache_id:
                    url_to_cache_id[norm] = cache_id
                    cache_id_to_norm[cache_id] = norm
                    try:
                        id_int = int(cache_id)
                        if id_int > max_id:
                            max_id = id_int
                    except ValueError:
                        pass
                if parent_id:
                    norm_to_parent_id[norm] = parent_id
                # 构建父子关系索引（全量）
                if parent_id:
                    parent_children.setdefault(parent_id, []).append(
                        (norm, layer, cache_id))
    return (url_to_rid, max_id, url_to_cache_id, dedup_keys,
            parent_children, cache_id_to_norm, norm_to_parent_id)


def append_processed_urls(entries):
    """追加已处理 URL 到本地缓存
    entries: [(id, url, title, date, record_id, parent_id, layer, 精选日期, date_added), ...]
    """
    headers = ['id', 'url', 'title', 'date', 'record_id',
               'parent_id', 'layer', '精选日期', 'date_added']
    need_header = not os.path.exists(PROCESSED_URLS_FILE) or \
                  os.path.getsize(PROCESSED_URLS_FILE) == 0
    with open(PROCESSED_URLS_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(headers)
        for entry in entries:
            writer.writerow(entry)


def init_processed_urls_from_bitable(client, wta_config):
    """首次初始化：从多维表格拉取 WTA 相关记录写入本地缓存

    排序规则：按 layer=0/1 记录的 date 升序，L2 子节点紧跟其父节点。
    精选日期使用记录自身的 date 字段值。

    Returns:
        (url_to_rid, max_id, url_to_cache_id, dedup_keys,
         parent_children, cache_id_to_norm, norm_to_parent_id)
    """
    bt_cfg = wta_config.get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    if not app_token or not table_id:
        print('  多维表格配置缺失，无法初始化本地缓存')
        return {}, 0, {}, set(), {}, {}, {}

    filter_str = 'CurrentValue.[精选合集].contains("WTA")'
    records = client.search_bitable_records(
        app_token, table_id,
        ['链接', '标题', '日期', '精选合集', '父记录'], filter_str)

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ── 第一遍：收集所有记录（不分配 cache_id） ──
    raw_list = []
    for rec in records:
        val = rec.get('链接', '')
        if isinstance(val, dict):
            val = val.get('link', '') or val.get('text', '')
        rid = rec.get('_record_id', '')
        if not val:
            continue
        url = str(val).strip()
        collection = rec.get('精选合集', '')
        if collection == 'WTA引用':
            layer = '2'
        elif collection == 'WTA-1o':
            layer = '0'
        else:
            layer = '1'
        parent_rids = rec.get('父记录', [])
        parent_rid = ''
        if isinstance(parent_rids, list):
            for item in parent_rids:
                if isinstance(item, dict):
                    ids = item.get('record_ids', [])
                    if isinstance(ids, list) and ids:
                        parent_rid = ids[0]
                        break
                elif isinstance(item, str):
                    parent_rid = item
                    break
        elif isinstance(parent_rids, str) and parent_rids:
            parent_rid = parent_rids
        date_val = rec.get('日期', '')
        if isinstance(date_val, (int, float)):
            date_val = str(int(date_val))
        raw_list.append({
            'url': url, 'rid': rid, 'title': rec.get('标题', ''),
            'date': date_val, 'layer': layer, 'parent_rid': parent_rid,
        })

    # ── 排序：orig/L1 按 date 升序，L2 紧跟父节点 ──
    rid_to_rec = {r['rid']: r for r in raw_list}
    # 建立父→子映射（仅 L2）
    parent_rid_to_children = {}
    for r in raw_list:
        if r['layer'] == '2' and r['parent_rid']:
            parent_rid_to_children.setdefault(
                r['parent_rid'], []).append(r)

    # 找到每个 L2 的"根"（顺着 parent_rid 往上找到 layer=0 或 1）
    def find_root_rid(r):
        visited = set()
        cur = r
        while cur['layer'] == '2' and cur['parent_rid']:
            if cur['parent_rid'] in visited:
                break
            visited.add(cur['parent_rid'])
            parent = rid_to_rec.get(cur['parent_rid'])
            if not parent:
                break
            cur = parent
        return cur['rid']

    # 收集所有顶层记录（layer=0 或 1，或无父/父不在集合中的）
    top_records = []
    for r in raw_list:
        if r['layer'] in ('0', '1'):
            top_records.append(r)
        elif r['layer'] == '2' and not r['parent_rid']:
            top_records.append(r)
        elif r['layer'] == '2' and r['parent_rid'] \
                not in rid_to_rec:
            top_records.append(r)

    # ── 计算精选日期：先分组，取组内 date 最大值 ──
    def _find_root(rid):
        visited = set()
        cur = rid
        while cur in rid_to_rec:
            pr = rid_to_rec[cur]['parent_rid']
            if not pr or pr not in rid_to_rec or pr in visited:
                break
            visited.add(cur)
            cur = pr
        return cur

    root_groups = {}
    for r in raw_list:
        root = _find_root(r['rid'])
        root_groups.setdefault(root, []).append(r)

    rid_to_sec_date = {}
    for root, members in root_groups.items():
        max_date = max(
            (str(m.get('date', '') or '') for m in members),
            default='')
        for m in members:
            rid_to_sec_date[m['rid']] = max_date

    # 按精选日期升序排列顶层记录
    top_records.sort(
        key=lambda x: rid_to_sec_date.get(x['rid'], '') or '99999999')

    # 按 orig → L1 → L2 展开
    sorted_list = []
    visited_rids = set()

    def append_with_children(r):
        if r['rid'] in visited_rids:
            return
        visited_rids.add(r['rid'])
        sorted_list.append(r)
        children = parent_rid_to_children.get(r['rid'], [])
        for child in children:
            append_with_children(child)

    for r in top_records:
        append_with_children(r)

    # 追加未被访问的记录（异常数据兜底）
    for r in raw_list:
        if r['rid'] not in visited_rids:
            visited_rids.add(r['rid'])
            sorted_list.append(r)

    # ── 分配 cache_id ──
    rid_to_cache_id = {}
    rid_to_layer = {}
    for seq, r in enumerate(sorted_list, 1):
        r['cache_id'] = str(seq).zfill(8)
        rid_to_cache_id[r['rid']] = r['cache_id']
        rid_to_layer[r['rid']] = r['layer']

    # ── 计算 parent_cache_id ──
    for r in sorted_list:
        parent_rid = r['parent_rid']
        if not parent_rid:
            r['parent_cache_id'] = ''
            continue
        parent_cache_id = rid_to_cache_id.get(parent_rid, '')
        parent_layer = rid_to_layer.get(parent_rid, '')
        if r['layer'] == '0':
            r['parent_cache_id'] = ''
        elif r['layer'] == '1':
            r['parent_cache_id'] = parent_cache_id
        elif r['layer'] == '2':
            if parent_layer == '0':
                l1_cid = ''
                for other in sorted_list:
                    if other.get('parent_rid') == parent_rid \
                            and other['layer'] == '1':
                        l1_cid = other['cache_id']
                        break
                if l1_cid:
                    r['parent_cache_id'] = l1_cid
                else:
                    print(f'  未找到 orig 的 L1 子节点: '
                          f'{r["url"][:60]}')
                    r['parent_cache_id'] = parent_cache_id
            elif parent_layer == '1':
                r['parent_cache_id'] = parent_cache_id
            else:
                print(f'  WTA引用父节点 layer 异常: '
                      f'{parent_layer}, url={r["url"][:60]}')
                r['parent_cache_id'] = parent_cache_id
        else:
            r['parent_cache_id'] = parent_cache_id

    # ── 构建缓存条目和索引 ──
    entries = []
    url_to_rid = {}
    url_to_cache_id = {}
    dedup_keys = set()
    parent_children = {}
    cache_id_to_norm = {}
    norm_to_parent_id = {}

    for r in sorted_list:
        pcid = r['parent_cache_id']
        norm = normalize_url(r['url'])
        sec_date = rid_to_sec_date.get(r['rid'], str(r.get('date', '')))
        entries.append((r['cache_id'], r['url'], r['title'], r['date'],
                        r['rid'], pcid, r['layer'], sec_date, now_str))
        url_to_rid[norm] = r['rid']
        url_to_cache_id[norm] = r['cache_id']
        cache_id_to_norm[r['cache_id']] = norm
        dedup_keys.add((norm, r['layer'], pcid))
        if pcid:
            norm_to_parent_id[norm] = pcid
            parent_children.setdefault(pcid, []).append(
                (norm, r['layer'], r['cache_id']))

    append_processed_urls(entries)
    print(f'  已初始化 {len(entries)} 条记录到本地缓存')
    return (url_to_rid, len(sorted_list), url_to_cache_id, dedup_keys,
            parent_children, cache_id_to_norm, norm_to_parent_id)


def parse_month_heading(text):
    """从 heading2 文本提取年月，如 '2026 年 2 月' -> (2026, 2)"""
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def parse_section_date(text, current_year, current_month=None):
    """从 heading3 文本提取日期，如 ' 3 月 6 日' -> 'YYYYMMDD'

    Args:
        text: heading3 文本
        current_year: 当前年份（从 heading2 或系统时间推断）
        current_month: 当前月份（从 heading2 推断，近期文档为 None）
    Returns:
        str: 'YYYYMMDD' 格式日期，解析失败返回 None
    """
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        return f'{current_year}{month:02d}{day:02d}'
    return None


def _get_block_text(block):
    """从 block 中提取纯文本"""
    for key in ('heading2', 'heading3', 'heading4', 'heading5',
                'bullet', 'text', 'ordered'):
        if key in block:
            elements = block[key].get('elements', [])
            return ''.join(el.get('text_run', {}).get('content', '')
                           for el in elements)
    return ''


def _extract_mention_docs(block):
    """从 bullet block 提取 mention_doc 元素
    Returns: [(url, title, token), ...]
    """
    results = []
    for key in ('bullet', 'text', 'ordered', 'quote'):
        if key not in block:
            continue
        for el in block[key].get('elements', []):
            md = el.get('mention_doc', {})
            if md.get('url'):
                results.append((
                    md['url'],
                    md.get('title', ''),
                    md.get('token', ''),
                ))
    return results


def _extract_text_links(block):
    """从 block 提取 text_run 中的超链接和纯文本中的 URL
    Returns: [(url, link_text, is_original), ...]
    is_original: 链接前面的文字包含"原文链接"
    """
    import re
    from urllib.parse import unquote

    # URL 正则匹配（排除中文字符和中文标点）
    url_pattern = re.compile(r'https?://[^\s<>\[\]\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+')

    results = []
    for key in ('bullet', 'text', 'ordered', 'quote'):
        if key not in block:
            continue
        elements = block[key].get('elements', [])
        preceding_text = ''
        plain_buf = ''  # 累积连续 non-link text_run，避免 URL 被飞书拆成多段

        for el in elements:
            tr = el.get('text_run', {})
            style = tr.get('text_element_style', {})
            link_url = style.get('link', {}).get('url', '')
            content = tr.get('content', '')

            if link_url:
                # 遇到超链接前，先统一处理累积的纯文本
                if plain_buf:
                    for u in url_pattern.findall(plain_buf):
                        results.append((u.rstrip('）'), u.rstrip('）'),
                                        '原文链接' in preceding_text))
                    preceding_text += plain_buf
                    plain_buf = ''
                # 超链接格式：link.url 是完整 URL，不受 text_run 分割影响
                is_orig = '原文链接' in preceding_text
                results.append((unquote(link_url), content, is_orig))
                preceding_text = ''
            else:
                # 累积纯文本，等到遇到超链接或块结束时统一提取
                plain_buf += content

        # 块结束，处理剩余纯文本
        if plain_buf:
            for u in url_pattern.findall(plain_buf):
                results.append((u.rstrip('）'), u.rstrip('）'),
                                '原文链接' in preceding_text))

    return results


def extract_urls_from_wta_doc(client, doc_token, start_date, end_date,
                               doc_type='recent'):
    """阶段一：从 WTA 文档提取 URL

    Args:
        doc_type: 'year'（年度文档，无分割线，有 heading2 年月）或 'recent'（近期文档，有分割线）
    Returns:
        list of dict: [{url, title, link_text, section_date, order,
                        doc_token, is_external}]
    """
    blocks = get_doc_blocks(client, doc_token)
    print(f'  文档 blocks: {len(blocks)}', flush=True)

    url_items = []
    seen_urls = set()
    current_year = datetime.now().year
    current_month = None
    current_date = None
    in_range = False
    order = 0

    # recent_doc 有分割线，需跳过分割线前的内容；year_doc 无分割线，直接解析
    past_divider = (doc_type != 'recent')  # 非 recent 文档无需等分割线

    for block in blocks:
        bt = block.get('block_type')
        text = _get_block_text(block)

        # recent_doc：等待分割线（block_type=3）后才开始解析
        if not past_divider:
            if bt == 3:  # divider
                past_divider = True
            continue

        # heading2 → 更新年月（仅 year_doc 有，如"2026 年 3 月"）
        if bt == 4:
            y, m = parse_month_heading(text)
            if y:
                current_year = y
                current_month = m
            continue

        # heading3 → 日期标题
        if bt == 5:
            section_date = parse_section_date(text, current_year, current_month)
            if section_date:
                current_date = section_date
                in_range = (start_date <= current_date <= end_date)
                order = 0
            continue

        # bullet（block_type=12）→ 提取条目
        if bt == 12 and in_range and current_date:
            order += 1
            # 优先提取 mention_doc
            mentions = _extract_mention_docs(block)
            for url, title, token in mentions:
                norm = normalize_url(url)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                url_items.append({
                    'url': url,
                    'title': title,
                    'link_text': title,
                    'section_date': current_date,
                    'order': order,
                    'doc_token': token,
                    'is_external': False,
                })

            # 补充 text_run link
            text_links = _extract_text_links(block)
            for url, link_text, _is_orig in text_links:
                if not url.startswith('http'):
                    continue
                norm = normalize_url(url)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                url_items.append({
                    'url': url,
                    'title': '',
                    'link_text': link_text,
                    'section_date': current_date,
                    'order': order,
                    'doc_token': '',
                    'is_external': True,
                })

    return url_items


def _is_feishu_internal_url(url):
    """判断是否为飞书内部链接（应排除）"""
    for domain in FEISHU_WIKI_DOMAINS:
        if domain in url:
            return True
    return False


def _is_feishu_internal_doc_url(url):
    """判断是否为应保留的飞书 wiki/docx 文档链接。"""
    url_lower = url.lower()
    return '.feishu.cn/wiki/' in url_lower or '.feishu.cn/docx/' in url_lower


def _is_invalid_url(url):
    """判断是否为无效 URL（非真实网页，应在提取阶段过滤）"""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ''
    except Exception:
        return True
    # localhost / 回环地址
    if host in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
        return True
    # 无 TLD 的域名（如 auth.py、brand-voice.md）
    if '.' in host:
        tld = host.rsplit('.', 1)[-1].lower()
        non_web_exts = ('py', 'md', 'js', 'ts', 'css', 'html', 'json',
                        'yaml', 'yml', 'toml', 'xml', 'txt', 'csv', 'log')
        if tld in non_web_exts:
            return True
    else:
        return True  # 无点号的 host，如纯单词
    return False


def extract_external_urls_from_wiki_page(client, doc_token):
    """第二层提取：从 wiki 子页面中提取外部链接和飞书子文档链接

    Returns:
        list of dict: [{url, link_text}, ...]
    """
    blocks = get_doc_blocks(client, doc_token)
    results = []
    seen = set()

    for block in blocks:
        # text_run link
        text_links = _extract_text_links(block)
        for url, link_text, is_original in text_links:
            if not url.startswith('http'):
                continue
            if _is_feishu_internal_doc_url(url):
                pass
            elif _is_feishu_internal_url(url) or _is_invalid_url(url):
                continue
            norm = normalize_url(url)
            if norm in seen:
                continue
            seen.add(norm)
            results.append({'url': url, 'link_text': link_text,
                            'is_original': is_original})

        # mention_doc（可能指向外部文档）
        mentions = _extract_mention_docs(block)
        for url, title, token in mentions:
            if not url.startswith('http'):
                continue
            if _is_feishu_internal_doc_url(url):
                pass
            elif _is_feishu_internal_url(url) or _is_invalid_url(url):
                continue
            norm = normalize_url(url)
            if norm in seen:
                continue
            seen.add(norm)
            results.append({'url': url, 'link_text': title,
                            'is_original': False})

    return results


def parse_wta_urls(url_items, parser, client, processed_urls=None,
                   dedup_keys=None, url_to_cache_id=None,
                   parent_children=None, cache_id_to_norm=None,
                   norm_to_parent_id=None):
    """阶段二：解析 URL + 第二层子页面提取 + 重试

    第二层外部链接紧邻其父级第一层 URL 输出。
    processed_urls: {normalized_url: record_id} 本地缓存，已存在的跳过解析但仍提取二级链接
    dedup_keys: {(norm, layer, parent_id)} 精选日期范围内的去重键，命中的一层 URL 跳过解析
    url_to_cache_id: {normalized_url: cache_id} 用于二层去重时查找父记录 cache_id
    parent_children: {parent_cache_id: [(norm_url, layer, cache_id), ...]}
    cache_id_to_norm: {cache_id: normalized_url}
    norm_to_parent_id: {normalized_url: parent_cache_id}

    Returns:
        (parsed_rows, error_rows)
        parsed_rows: [{链接, 标题, 日期, 星期, 来源, 异常信息}, ...]
    """
    if processed_urls is None:
        processed_urls = {}
    if dedup_keys is None:
        dedup_keys = set()
    if url_to_cache_id is None:
        url_to_cache_id = {}
    if parent_children is None:
        parent_children = {}
    if cache_id_to_norm is None:
        cache_id_to_norm = {}
    if norm_to_parent_id is None:
        norm_to_parent_id = {}
    # 构建去重 URL 集合（不区分 layer 和 parent），用于快速判断 URL 是否已处理
    parsed_rows = []
    retry_indices = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    seen_urls = set()

    for idx, item in enumerate(url_items):
        url = item['url']
        norm_url = normalize_url(url)
        l1_in_cache = norm_url in processed_urls
        l1_deduped = (not l1_in_cache and
                      (norm_url, '1', '') in dedup_keys)
        skip_tag = '  (已存在)' if l1_in_cache else \
                   '  (已去重)' if l1_deduped else ''
        print(f'\n[{idx+1}/{len(url_items)}] {url[:80]}{skip_tag}')

        section_date = item['section_date']

        if l1_in_cache or l1_deduped:
            # 一级链接已处理过或已去重，跳过解析，但仍需提取二级链接
            seen_urls.add(norm_url)

        # 构建缓存子节点清单（仅 --update 模式下的候选重复项）
        cached_children_norms = set()
        parent_check_norm = None
        if dedup_keys and (l1_in_cache or l1_deduped):
            cid = url_to_cache_id.get(norm_url)
            pid = norm_to_parent_id.get(norm_url)
            if cid and parent_children.get(cid):
                # 当前 URL 有子节点（layer=1）→ 取自身的子节点
                for child in parent_children.get(cid, []):
                    cached_children_norms.add(child[0])
                if pid:
                    parent_check_norm = cache_id_to_norm.get(pid)
            elif pid:
                # 当前 URL 是 layer=2 → 取父节点的所有子节点
                parent_check_norm = cache_id_to_norm.get(pid)
                for child in parent_children.get(pid, []):
                    cached_children_norms.add(child[0])

        if not (l1_in_cache or l1_deduped):
            try:
                article = parser.parse_url(url, link_text=item['link_text'])
            except Exception as e:
                print(f'  ⚠ 解析失败（{type(e).__name__}），等待5秒后继续')
                time.sleep(5)
                article = {'title': '', 'publish_date': '', 'source': '',
                            'url': url, 'error_info': f'网络异常: {type(e).__name__}'}

            title = item['title'] or article['title']
            if not title or title.lower() in ('record', 'untitled'):
                if item['link_text']:
                    title = f'（{item["link_text"]}）'

            pub_date = article['publish_date']
            if pub_date and section_date:
                final_date = min(pub_date, section_date)
            else:
                final_date = pub_date or section_date

            weekday = calc_weekday(final_date) if pub_date and final_date else '\\'

            row = {
                '链接': normalize_url(article['url']),
                '标题': title,
                '日期': final_date,
                '星期': weekday,
                '来源': article['source'],
                '异常信息': article.get('error_info', ''),
                '_layer': 1,
                '_parent_url': None,
                '_精选日期': section_date,
            }
            parsed_rows.append(row)
            seen_urls.add(normalize_url(article['url']))

            print(f'  标题: {title[:60]}')
            print(f'  来源: {article["source"]}  日期: {final_date}')

            err = article.get('error_info', '')
            url_lower = url.lower()
            if err and '未配置' not in err \
                    and not any(d in url_lower for d in SKIP_RETRY_DOMAINS):
                retry_indices.append(('pending', row))

        # 第二层：子页面外部链接（无论一级是否已缓存都提取）
        l2_original = []
        l2_others = []
        l2_skipped = 0
        if item['doc_token'] and not item['is_external']:
            print(f'  提取子页面外部链接...')
            try:
                ext_urls = extract_external_urls_from_wiki_page(
                    client, item['doc_token'])
            except Exception as e:
                print(f'  ⚠ 子页面提取失败（{type(e).__name__}），等待5秒后继续')
                time.sleep(5)
                ext_urls = []
            if ext_urls:
                print(f'  发现 {len(ext_urls)} 个外部链接')
            for eu in ext_urls:
                eu_url = eu['url']
                eu_norm = normalize_url(eu_url)
                # 仅与当前父节点的缓存子节点去重
                if cached_children_norms:
                    # 2c1: layer=2 时，判断是否与父节点 URL 一致
                    if parent_check_norm and eu_norm == parent_check_norm:
                        l2_skipped += 1
                        seen_urls.add(eu_norm)
                        continue
                    # 2c2: 判断是否在缓存子节点清单中
                    if eu_norm in cached_children_norms:
                        l2_skipped += 1
                        seen_urls.add(eu_norm)
                        continue
                seen_urls.add(eu_norm)

                print(f'    [L2] {eu_url[:80]}')
                a2 = parser.parse_url(eu_url, link_text=eu['link_text'])

                t2 = a2['title']
                if not t2:
                    t2 = f'（{eu["link_text"]}）'

                pd2 = a2['publish_date']
                fd2 = pd2 or section_date
                wd2 = calc_weekday(pd2) if pd2 else '\\'

                e2 = a2.get('error_info', '')
                if e2 and t2 and fd2:
                    parts = [p.strip() for p in e2.split('、')]
                    parts = [p for p in parts if p not in ('未提取标题', '未提取日期')]
                    e2 = '、'.join(parts)

                is_orig = eu.get('is_original', False)
                row2 = {
                    '链接': normalize_url(a2['url']),
                    '标题': t2,
                    '日期': fd2,
                    '星期': wd2,
                    '来源': a2['source'],
                    '异常信息': e2,
                    '_layer': 2 if not is_orig else 'orig',
                    '_parent_url': None,
                    '_精选日期': section_date,
                }
                if is_orig:
                    l2_original.append(row2)
                else:
                    l2_others.append(row2)
                seen_urls.add(normalize_url(a2['url']))
                print(f'      标题: {t2[:60]}')

                eu_lower = eu_url.lower()
                if e2 and '未配置' not in e2 \
                        and not any(d in eu_lower for d in SKIP_RETRY_DOMAINS):
                    retry_indices.append(('pending', row2))

                time.sleep(1)

            if l2_skipped:
                print(f'  二层已去重跳过: {l2_skipped} 条')

        # 判断 L1 整体是否完全重复（所有二级 URL 均已存在）
        if (l1_in_cache or l1_deduped) and cached_children_norms \
                and not l2_original and not l2_others:
            print(f'  所有二级URL均已存在，跳过')
            continue

        # 设置父记录关系和插入顺序
        if l1_in_cache or l1_deduped:
            # 一级已缓存，二级链接的父记录指向一级的 URL
            cached_l1_url = normalize_url(url)
            for r in l2_original:
                r['_parent_url'] = None
            for r in l2_others:
                r['_parent_url'] = cached_l1_url  # 缓存 parent_id → L1
            # 如果有原文链接，多维表格父记录指向 orig
            if l2_original:
                orig_url = l2_original[0]['链接']
                for r in l2_others:
                    r['_bitable_parent_url'] = orig_url
            for r in l2_original:
                parsed_rows.append(r)
            for r in l2_others:
                parsed_rows.append(r)
        else:
            # 一级是新的，走原有插入逻辑
            l1_url = row['链接']
            if l2_original:
                orig_url = l2_original[0]['链接']
                row['_parent_url'] = orig_url
                for r in l2_original:
                    r['_parent_url'] = None
                for r in l2_others:
                    r['_parent_url'] = l1_url  # 缓存 parent_id → L1
                    r['_bitable_parent_url'] = orig_url  # 多维表格父记录 → orig
            else:
                for r in l2_others:
                    r['_parent_url'] = l1_url

            parsed_rows.pop()
            for r in l2_original:
                parsed_rows.append(r)
            parsed_rows.append(row)
            for r in l2_others:
                parsed_rows.append(r)

        # 修正 retry_indices：把 pending 的替换为实际 index
        new_retry = []
        for ri in retry_indices:
            if isinstance(ri, tuple) and ri[0] == 'pending':
                target_row = ri[1]
                for i, pr in enumerate(parsed_rows):
                    if pr is target_row:
                        new_retry.append(i)
                        break
            else:
                new_retry.append(ri)
        retry_indices = new_retry

        time.sleep(1)

    # --- 第二轮重试 ---
    if retry_indices:
        print(f'\n{"=" * 40}')
        print(f'重试解析失败的 URL ({len(retry_indices)} 条)')
        print('=' * 40)
        time.sleep(3)

        for retry_seq, ri in enumerate(retry_indices):
            old_row = parsed_rows[ri]
            url = old_row['链接']
            print(f'\n[重试 {retry_seq+1}/{len(retry_indices)}] {url[:80]}')

            article = parser.parse_url(url)
            if not article.get('error_info') or \
               len(article.get('error_info', '')) < len(old_row.get('异常信息', '')):
                title = article['title'] or old_row['标题']
                fd = article['publish_date'] or old_row['日期']
                parsed_rows[ri] = {
                    '链接': normalize_url(article['url']),
                    '标题': title,
                    '日期': fd,
                    '星期': calc_weekday(fd) if fd else old_row['星期'],
                    '来源': article['source'] or old_row['来源'],
                    '异常信息': article.get('error_info', ''),
                    '_layer': old_row.get('_layer', 1),
                    '_parent_url': old_row.get('_parent_url'),
                    '_精选日期': old_row.get('_精选日期', ''),
                }
                print(f'  重试成功: {title[:60]}')
            else:
                print(f'  重试仍失败: {article.get("error_info", "")[:60]}')

            time.sleep(1)

    # --- 收集错误 ---
    error_rows = []
    for row in parsed_rows:
        if row.get('异常信息'):
            error_rows.append({**row, '记录时间': now_str})

    return parsed_rows, error_rows


def write_to_bitable(client, parsed_rows, wta_config, dedup_keys,
                     url_to_cache_id):
    """阶段三：去重写入飞书多维表格

    Args:
        dedup_keys: {(normalized_url, layer, parent_cache_id)} 精确去重
        url_to_cache_id: {normalized_url: cache_id} 查找父记录缓存 ID

    Returns:
        (new_records, parent_urls, cache_parent_urls, section_dates,
         created_records, write_ok)
        write_ok:
          - True: 无需写入，或待写记录已全部写入成功
          - False: 配置缺失，或存在写入失败
    """
    bt_cfg = wta_config.get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    if not app_token or not table_id:
        print('  多维表格配置缺失，跳过写入')
        return [], [], [], [], [], False

    print(f'\n本地缓存已有 {len(dedup_keys)} 条去重记录')

    new_records = []
    parent_urls = []       # 多维表格父记录用
    cache_parent_urls = [] # 缓存 parent_id 用
    section_dates = []
    skipped = 0
    skipped_items = []

    for idx, row in enumerate(parsed_rows):
        url = row['链接']
        norm = normalize_url(url)
        layer_val = row.get('_layer', 1)
        layer = '2' if layer_val == 2 else '0' if layer_val == 'orig' else '1'
        parent_url = row.get('_parent_url')
        parent_cache_id = ''
        if parent_url:
            parent_cache_id = url_to_cache_id.get(
                normalize_url(parent_url), '')

        dedup_key = (norm, layer, parent_cache_id)
        if dedup_key in dedup_keys:
            skipped += 1
            skipped_items.append({
                'url': url, 'title': row.get('标题', ''),
                'date': row.get('日期', ''), 'layer': layer,
                'parent_url': parent_url,
            })
            continue

        fields = {
            '链接': url,
            '标题': row['标题'],
            '来源': row['来源'],
            '精选合集': 'WTA引用' if row.get('_layer') == 2 else
                        'WTA-1o' if row.get('_layer') == 'orig' else 'WTA-1',
        }
        if row['日期']:
            fields['日期'] = int(row['日期']) if str(row['日期']).isdigit() else row['日期']
        if row['星期']:
            fields['星期'] = row['星期']
        new_records.append(fields)
        parent_urls.append(row.get('_bitable_parent_url') or row.get('_parent_url'))
        cache_parent_urls.append(row.get('_parent_url'))
        section_dates.append(row.get('_精选日期', ''))

    if skipped:
        print(f'  跳过已存在: {skipped} 条')
        for item in skipped_items:
            url = item['url']
            title = item['title'][:40] if item['title'] else '(无标题)'
            date = item['date'] or '-'
            if item['layer'] == '2' and item['parent_url']:
                p_url = item['parent_url'][:50]
                print(f'    [L2] {url[:60]}  {title}  {date}  → {p_url}')
            else:
                print(f'    [L1] {url[:60]}  {title}  {date}')

    if not new_records:
        print('  无新记录需要写入')
        return [], [], [], [], [], True

    print(f'  准备写入 {len(new_records)} 条新记录...')
    result = client.batch_add_bitable_records(app_token, table_id, new_records)
    print(f'  写入完成: 成功 {result["success"]}, 失败 {result["failed"]}')

    created_records = result.get('records', [])
    write_ok = (result["failed"] == 0 and
                len(created_records) == len(new_records))
    return (new_records, parent_urls, cache_parent_urls,
            section_dates, created_records, write_ok)


def _set_parent_records(client, app_token, table_id,
                        new_records, parent_urls, created_records,
                        processed_urls):
    """根据 _parent_url 批量设置父记录关联

    Args:
        processed_urls: {normalized_url: record_id} 包含历史+本次新写入的映射
    """
    # 建立本次新写入的 url -> record_id 映射
    url_to_rid = {}
    for i, rec in enumerate(created_records):
        rid = rec.get('record_id', '')
        if rid and i < len(new_records):
            url = new_records[i].get('链接', '')
            if url:
                url_to_rid[normalize_url(url)] = rid

    # 收集需要更新的记录
    updates = []
    for i, parent_url in enumerate(parent_urls):
        if not parent_url or i >= len(created_records):
            continue
        child_rid = created_records[i].get('record_id', '')
        norm_parent = normalize_url(parent_url)
        # 优先从本次写入查找，再从本地缓存查找
        parent_rid = url_to_rid.get(norm_parent) or processed_urls.get(norm_parent)
        if child_rid and parent_rid:
            updates.append({'record_id': child_rid, 'parent_rid': parent_rid})

    if not updates:
        return

    print(f'  设置父记录关联: {len(updates)} 条')
    # 批量更新（每次最多 500 条）
    for i in range(0, len(updates), 500):
        batch = updates[i:i + 500]
        payload = {
            'records': [
                {
                    'record_id': u['record_id'],
                    'fields': {'父记录': [u['parent_rid']]}
                }
                for u in batch
            ]
        }
        client._request(
            'POST',
            f'/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update',
            json=payload
        )
        time.sleep(0.3)  # 等待飞书完成位置调整
    print(f'  父记录关联设置完成')


def write_urls_csv(url_items, date_tag):
    """阶段一临时文件"""
    filepath = os.path.join(LOG_ERR_DIR, f'wta_urls_{date_tag}.csv')
    headers = ['序号', 'URL', '标题', '链接文字', '精选日期', 'doc_token', '外部链接']
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for i, item in enumerate(url_items, 1):
            writer.writerow({
                '序号': i,
                'URL': item['url'],
                '标题': item['title'],
                '链接文字': item['link_text'],
                '精选日期': item['section_date'],
                'doc_token': item['doc_token'],
                '外部链接': '是' if item['is_external'] else '否',
            })
    print(f'  已写入 {filepath} ({len(url_items)} 条)')


def write_parsed_csv(parsed_rows, date_tag):
    """阶段二临时文件"""
    filepath = os.path.join(LOG_ERR_DIR, f'wta_parsed_{date_tag}.csv')
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=PARSED_HEADERS)
        writer.writeheader()
        for row in parsed_rows:
            out = {h: row.get(h, '') for h in PARSED_HEADERS}
            out['精选日期'] = row.get('_精选日期', '')
            writer.writerow(out)
    return len(parsed_rows)


def write_error_log(error_rows, date_tag):
    """错误日志"""
    if not error_rows:
        return 0
    filepath = os.path.join(LOG_ERR_DIR, f'wta_errors_{date_tag}.csv')
    headers = ['链接', '标题', '日期', '来源', '精选日期', '异常信息', '记录时间']
    need_header = not os.path.exists(filepath) or os.path.getsize(filepath) == 0
    if need_header:
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in error_rows:
                out = {h: row.get(h, '') for h in headers}
                out['精选日期'] = row.get('_精选日期', '')
                writer.writerow(out)
    else:
        with open(filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            for row in error_rows:
                out = {h: row.get(h, '') for h in headers}
                out['精选日期'] = row.get('_精选日期', '')
                writer.writerow(out)
    return len(error_rows)


def _normalize_date(d):
    """6位日期自动补全为8位：250226 -> 20250226"""
    if len(d) == 6 and d.isdigit():
        return '20' + d
    return d


def main():
    """主函数"""
    _setup_encoding()

    ap = argparse.ArgumentParser(description='WaytoAGI 知识库 → 飞书多维表格')
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--his', nargs=2, metavar=('START', 'END'),
                       help='历史批量: --his 20260220 20260220')
    group.add_argument('--update', action='store_true',
                       help='增量更新: 从 last_processed_date 到今天')
    args = ap.parse_args()

    print('=' * 60, flush=True)
    print('WaytoAGI 知识库 → 飞书多维表格')
    print('=' * 60, flush=True)

    # 初始化客户端
    client = FeishuClient()
    credentials = {
        'zsxq_token': client.credentials.get('zsxq', {}).get('access_token', ''),
        'zhihu_cookies': client.credentials.get('zhihu', {}),
        'feishu_user_token': client.credentials.get('auth', {}).get('user_access_token', ''),
        'wechat_cookie': client.credentials.get('wechat', {}).get('cookie', ''),
        'xiaobot_token': client.credentials.get('xiaobot', {}).get('token', ''),
    }
    parser = UrlParser(credentials=credentials)

    # 检查 token
    if not client.check_token_valid():
        print('\nToken 过期，尝试刷新...')
        if not client.refresh_access_token():
            print('Token 刷新失败，请运行: python src/modules/feishu_auth.py')
            return
    print('Token 有效')

    wta_config = client.config.get('waytoagi', {})

    # 动态设置缓存文件路径（按 table_id 命名）
    global PROCESSED_URLS_FILE
    bt_cfg = wta_config.get('target_bitable', {})
    wta_table_id = bt_cfg.get('table_id', '')
    if wta_table_id:
        PROCESSED_URLS_FILE = os.path.join(
            DATA_DIR, f'wta_cache_{wta_table_id}.csv')
    else:
        PROCESSED_URLS_FILE = os.path.join(DATA_DIR, 'wta_cache_default.csv')

    bitable_cache = None
    if wta_table_id:
        bitable_cache = BitableUrlCache(wta_table_id, DATA_DIR)
        bt_urls, _ = bitable_cache.load()
        print(f'  bitable_url_cache 已加载 {len(bt_urls)} 条', flush=True)

    # 确定日期范围（需在缓存加载前，用于过滤 dedup_keys）
    if args.his:
        start_date = _normalize_date(args.his[0])
        end_date = _normalize_date(args.his[1])
    else:
        last = str(wta_config.get('last_processed_date', ''))
        if not last:
            print('未找到 last_processed_date，请先用 --his 模式运行')
            return
        lookback_days = wta_config.get('lookback_days', 2)
        last_dt = datetime.strptime(last, '%Y%m%d')
        start_dt = last_dt - timedelta(days=lookback_days)
        start_date = start_dt.strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')

    print(f'日期范围: {start_date} ~ {end_date}')
    date_tag = datetime.now().strftime('%Y%m%d')

    # 加载本地 URL 缓存（按精选日期范围过滤 dedup_keys）
    if os.path.exists(PROCESSED_URLS_FILE):
        url_to_rid, max_id, url_to_cache_id, dedup_keys, \
            parent_children, cache_id_to_norm, norm_to_parent_id = \
            load_processed_urls(start_date, end_date)
        print(f'本地缓存: {len(url_to_rid)} 条 URL, 去重范围内 {len(dedup_keys)} 条')
    else:
        print('本地缓存不存在，从多维表格初始化...')
        url_to_rid, max_id, url_to_cache_id, dedup_keys, \
            parent_children, cache_id_to_norm, norm_to_parent_id = \
            init_processed_urls_from_bitable(client, wta_config)
        print(f'初始化完成: {len(url_to_rid)} 条 URL')

    # 决定扫描哪些文档：年度文档优先，找不到再扫近期文档
    year_doc = wta_config.get('year_doc', '')
    recent_doc = wta_config.get('recent_doc', '')
    doc_tokens = []
    if year_doc:
        doc_tokens.append(('year', year_doc))
    if recent_doc:
        doc_tokens.append(('recent', recent_doc))

    # ── 阶段一：提取 URL ──
    print(f'\n阶段一：提取 URL', flush=True)
    all_url_items = []
    seen_urls = set()

    for doc_type, doc_token in doc_tokens:
        print(f'\n  扫描文档 ({doc_type}): {doc_token}', flush=True)
        items = extract_urls_from_wta_doc(
            client, doc_token, start_date, end_date, doc_type)
        # 跨文档去重（年度文档优先）
        for item in items:
            norm = normalize_url(item['url'])
            if norm not in seen_urls:
                seen_urls.add(norm)
                all_url_items.append(item)
        print(f'  提取到 {len(items)} 条，去重后累计 {len(all_url_items)} 条')

    if not all_url_items:
        print('\n未提取到任何 URL，结束')
        return

    # 按日期升序（最远日期在前），同日期按 order 升序
    all_url_items.sort(key=lambda x: (x['section_date'], x['order']))

    write_urls_csv(all_url_items, date_tag)

    # ── 阶段二：解析 URL ──
    print(f'\n阶段二：解析 URL', flush=True)
    # --update 模式传入 dedup_keys，对已去重的一层 URL 跳过解析
    pre_dedup = dedup_keys if not args.his else None
    pre_pc = parent_children if not args.his else None
    pre_ci = cache_id_to_norm if not args.his else None
    pre_np = norm_to_parent_id if not args.his else None
    parsed_rows, error_rows = parse_wta_urls(
        all_url_items, parser, client, url_to_rid, pre_dedup,
        url_to_cache_id, pre_pc, pre_ci, pre_np)
    n_parsed = write_parsed_csv(parsed_rows, date_tag)
    print(f'  已写入 {n_parsed} 条')
    n_err = write_error_log(error_rows, date_tag)
    if n_err:
        print(f'  错误日志已写入 {n_err} 条')

    # ── 阶段三：写入多维表格 ──
    print(f'\n阶段三：写入多维表格', flush=True)
    new_records, parent_urls, cache_parent_urls, section_dates, created_records, write_ok = \
        write_to_bitable(
            client, parsed_rows, wta_config, dedup_keys, url_to_cache_id)

    # 写入本地缓存
    if created_records:
        print(f'\n  写入本地缓存')
        app_token = bt_cfg.get('app_token', '')
        table_id = wta_table_id

        cache_entries = []
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for i, rec in enumerate(created_records):
            rid = rec.get('record_id', '')
            if not rid or i >= len(new_records):
                continue
            max_id += 1
            cache_id = str(max_id).zfill(8)
            url = new_records[i].get('链接', '')
            title = new_records[i].get('标题', '')
            date_val = new_records[i].get('日期', '')
            parent_url = cache_parent_urls[i] if i < len(cache_parent_urls) else None
            parent_cache_id = ''
            if parent_url:
                parent_cache_id = url_to_cache_id.get(
                    normalize_url(parent_url), '')
            collection = new_records[i].get('精选合集', '')
            if collection == 'WTA引用':
                layer = '2'
            elif collection == 'WTA-1o':
                layer = '0'
            else:
                layer = '1'
            sec_date = section_dates[i] if i < len(section_dates) else ''
            cache_entries.append((cache_id, url, title, date_val, rid,
                                  parent_cache_id, layer, sec_date, now_str))
            norm = normalize_url(url)
            url_to_cache_id[norm] = cache_id
            url_to_rid[norm] = rid
            dedup_keys.add((norm, layer, parent_cache_id))

        if cache_entries:
            append_processed_urls(cache_entries)
            print(f'    追加 {len(cache_entries)} 条 → {PROCESSED_URLS_FILE}')

        # 同步写入 bitable_url_cache
        if bitable_cache and created_records:
            bt_cache_entries = []
            for i, rec in enumerate(created_records):
                rid = rec.get('record_id', '')
                url = new_records[i].get('链接', '') if i < len(new_records) else ''
                if url:
                    bt_cache_entries.append({'url': url, 'record_id': rid})
            if bt_cache_entries:
                bitable_cache.append(bt_cache_entries)
                print(f'    追加 {len(bt_cache_entries)} 条 → {os.path.basename(bitable_cache._file)}')

        # 设置父记录关联
        if any(parent_urls):
            _set_parent_records(client, app_token, table_id,
                                new_records, parent_urls, created_records,
                                url_to_rid)

    latest_processed_date = max(
        (item.get('section_date', '') for item in all_url_items if item.get('section_date')),
        default=''
    )

    # 仅在阶段三成功时推进 last_processed_date，避免写入失败后跳过未入库数据
    if write_ok:
        old_val = str(wta_config.get('last_processed_date', ''))
        target_date = latest_processed_date or end_date
        if old_val:
            set_config_value_preserve_comments(
                client.config_path, ['waytoagi', 'last_processed_date'], target_date)
        wta_config['last_processed_date'] = target_date
        print(f'\n已更新 last_processed_date: {target_date}')
    else:
        print(f'\n未更新 last_processed_date，保持原值: {wta_config.get("last_processed_date", "")}')

    # 汇总统计
    print(f'\n{"=" * 60}')
    print('处理完成')
    print('=' * 60)
    print(f'  提取 URL: {len(all_url_items)} 条')
    print(f'  解析结果: {len(parsed_rows)} 条')
    print(f'  异常: {len(error_rows)} 条')


if __name__ == '__main__':
    main()
