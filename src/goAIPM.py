"""
星球周报文档 → 飞书多维表格
从飞书 wiki 周报文档中提取 URL，解析文章信息，写入飞书多维表格。

用法:
  python src/goAIPM.py --file <url>          # 处理单个周报文档
  python src/goAIPM.py --list <file>         # 批量处理周报文档列表
  python src/goAIPM.py --daily <url>         # 处理单个日报文档
  python src/goAIPM.py --update              # 自动处理新日报（基于 last_processed_date）
  python src/goAIPM.py --weekly <url>        # 基于周报 wiki 完善多维表格
  python src/goAIPM.py --towiki <src> <dst>  # 把网页/PDF 内容写入飞书 wiki

参数:
  --file URL         单个周报文档 URL
  --list FILE        周报文档列表文件路径
  --daily URL        单个日报文档 URL（zsxq 短链或直链）
  --update           自动处理新日报（基于 last_processed_date）
  --weekly URL       基于周报 wiki 完善多维表格
  --towiki SRC DST   源网页 URL 或 PDF 文件路径，目标飞书 wiki/docx URL

输出文件:
  log-err/aipm_parse_error_log.csv   解析失败日志
  log-err/aipm_url-fail.log          URL 获取失败日志
  data/aipm/aipm_daily_urls.csv      日报提取的 URL 列表
  data/aipm/aipm_weekly_urls_N.csv   周报提取的 URL 列表
  data/aipm/aipm_weekly_parsed_N.csv 周报解析结果
  data/bitable_cache_*.csv           多维表格 URL 缓存
"""

import csv
import json
import os
import re
import sys
import io
import time
import argparse
import requests
import mimetypes
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from bs4 import BeautifulSoup

# Windows UTF-8 输出（仅在直接运行时生效）
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
from modules.bitable_url_cache import BitableUrlCache
from modules.config_utils import (
    format_unix_ts_comment,
    set_config_value_preserve_comments,
)

DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
AIPM_DIR = os.path.join(DATA_DIR, 'aipm')
TEMP_DIR = os.path.join(PROJECT_ROOT, 'temp')
os.makedirs(AIPM_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# 临时文件路径
ERROR_LOG_FILE = os.path.join(PROJECT_ROOT, 'log-err', 'aipm_parse_error_log.csv')
URL_FAIL_LOG = os.path.join(PROJECT_ROOT, 'log-err', 'aipm_url-fail.log')

# 重试无意义的域名（结构性失败，非偶发网络问题）
SKIP_RETRY_DOMAINS = ['github.com', 'huggingface.co', '127.0.0.1', 'localhost']

# 排除的文档段落关键词
EXCLUDE_KEYWORDS = ['AIGC早报_', 'AI日报_', '报告下载']

# CSV 表头
PARSED_HEADERS = ['链接', '标题', '日期', '星期', '来源', '精选合集', '周报时间', '异常信息']


class ZsxqAuthError(RuntimeError):
    """知识星球认证失效。"""

    pass


def get_tenant_headers(client):
    """获取 tenant token 请求头"""
    token = client._get_tenant_access_token()
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json; charset=utf-8'
    }


def get_doc_title(client, doc_token):
    """获取文档标题"""
    headers = get_tenant_headers(client)
    resp = requests.get(
        f'https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}',
        headers=headers, timeout=30
    )
    data = resp.json()
    if data.get('code') == 0:
        return data['data']['document']['title']
    return ''


def get_doc_blocks(client, doc_token):
    """获取文档所有 blocks"""
    headers = get_tenant_headers(client)
    all_blocks = []
    page_token = None
    while True:
        params = {'page_size': 500}
        if page_token:
            params['page_token'] = page_token
        try:
            resp = requests.get(
                f'https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/blocks',
                headers=headers, params=params, timeout=30
            )
        except (ConnectionError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            print(f'  获取 blocks 连接异常，5秒后重试: {e}')
            time.sleep(5)
            headers = get_tenant_headers(client)  # 刷新 token
            try:
                resp = requests.get(
                    f'https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/blocks',
                    headers=headers, params=params, timeout=30
                )
            except Exception as e2:
                print(f'  获取 blocks 重试失败: {e2}')
                break
        result = resp.json()
        if result.get('code') != 0:
            print(f'  获取 blocks 失败: {result.get("msg")}')
            break
        items = result.get('data', {}).get('items', [])
        all_blocks.extend(items)
        page_token = result.get('data', {}).get('page_token')
        if not page_token:
            break
    return all_blocks


def extract_weekly_time(doc_title):
    """从文档标题提取周报时间，如 '_p_星球周报_第360期_20250510' -> '250510周'"""
    m = re.search(r'期_20(\d{4})(\d{2})', doc_title)
    if m:
        return f'{m.group(1)}{m.group(2)}周'
    return ''


def parse_block_elements(elements):
    """解析 block 的 elements，返回 (full_text, links, has_important)
    links: [(url, link_text), ...]
    """
    full_text = ''
    links = []
    has_important = False
    for el in elements:
        tr = el.get('text_run', {})
        content = tr.get('content', '')
        style = tr.get('text_element_style', {})
        link_url = style.get('link', {}).get('url', '')
        full_text += content
        if link_url:
            links.append((unquote(link_url), content))
        if '【重要】' in content:
            has_important = True
    return full_text, links, has_important


def should_exclude_block(full_text):
    """判断 block 是否应排除（早报、报告下载等）"""
    for kw in EXCLUDE_KEYWORDS:
        if kw in full_text:
            return True
    return False


def is_hanniman_comment(full_text):
    """判断是否为 hanniman 评注行"""
    return full_text.strip().startswith('hanniman评注')


def is_reference_block(full_text):
    """判断是否为引用行（hanniman评注 或 隐身阅读）"""
    stripped = full_text.strip()
    return stripped.startswith('hanniman评注') or stripped.startswith('隐身阅读')


# hanniman 评注中应排除的链接关键词（日报引用等）
HANNIMAN_EXCLUDE = ['日报_', 'AI日报']


def extract_urls_from_doc(client, doc_token):
    """阶段一：从文档提取所有 URL 及上下文信息

    Returns:
        list of dict: [{url, link_text, full_text, has_important, weekly_time}, ...]
    """
    doc_title = get_doc_title(client, doc_token)
    weekly_time = extract_weekly_time(doc_title)
    print(f'  文档标题: {doc_title}', flush=True)
    print(f'  周报时间: {weekly_time}')

    blocks = get_doc_blocks(client, doc_token)
    print(f'  文档 blocks: {len(blocks)}', flush=True)

    url_items = []
    seen_urls = set()
    exclude_mode = False
    skip_daily_section = False  # 跳过日报汇总整个章节

    for block in blocks:
        # 获取 elements，同时记录 block 类型
        block_key = None
        elements = None
        for key in ('bullet', 'text', 'ordered', 'heading1', 'heading2',
                     'heading3', 'heading4', 'heading5', 'heading6'):
            if key in block:
                block_key = key
                elements = block[key].get('elements', [])
                break
        if not elements:
            continue

        full_text, links, has_important = parse_block_elements(elements)

        # 日报汇总章节：整体跳过
        if 'AI日报汇总' in full_text or 'AIGC早报汇总' in full_text:
            skip_daily_section = True
            continue
        if skip_daily_section:
            # 遇到同级或更高 heading 退出跳过模式
            if block_key in ('heading3', 'heading2', 'heading1') and full_text.strip():
                skip_daily_section = False
            else:
                continue

        # 含"报告下载"的行：跳过该条目（不跳过整个章节）
        if '报告下载' in full_text:
            continue

        # 检查是否进入/退出排除区域
        if should_exclude_block(full_text) and not has_important:
            exclude_mode = True
            continue
        # 遇到新的编号条目（如 "1、"）且不含排除关键词，退出排除模式
        if exclude_mode and re.match(r'^\d+、', full_text.strip()):
            if not should_exclude_block(full_text):
                exclude_mode = False

        if exclude_mode:
            continue

        # hanniman 评注中的链接：排除日报引用，保留其他
        is_ref = is_reference_block(full_text)
        if is_hanniman_comment(full_text):
            filtered_links = []
            for url, link_text in links:
                skip = False
                for kw in HANNIMAN_EXCLUDE:
                    if kw in link_text:
                        skip = True
                        break
                if not skip:
                    filtered_links.append((url, link_text))
            links = filtered_links
            if not links:
                continue

        if not links:
            continue

        for url, link_text in links:
            # URL 去重
            if url in seen_urls:
                continue
            seen_urls.add(url)

            url_items.append({
                'url': url,
                'link_text': link_text,
                'full_text': full_text,
                'has_important': has_important,
                'is_reference': is_ref,
                'weekly_time': weekly_time,
            })

    return url_items


# ── 日报处理相关 ──────────────────────────────────────────

# 日报文章中应排除的链接域名（需同时满足链接文字含"日报"或"周报"）
DAILY_EXCLUDE_DOMAINS = ['zsxq.com', 'shimo.im']
ZSXQ_SESSION = requests.Session()
ZSXQ_SESSION.trust_env = False


def extract_daily_links_from_doc(client, doc_token):
    """从周报文档的"AIGC早报汇总"章节提取日报短链接

    Returns:
        list of dict: [{short_url, link_text, daily_date}, ...]
        daily_date 从链接文字中提取，如 'AIGC早报_20250530' -> '20250530'
    """
    blocks = get_doc_blocks(client, doc_token)
    daily_items = []
    in_daily = False

    for block in blocks:
        block_key = None
        elements = None
        for key in ('bullet', 'text', 'ordered', 'heading3', 'heading4',
                     'heading5', 'heading6'):
            if key in block:
                block_key = key
                elements = block[key].get('elements', [])
                break
        if not elements:
            continue

        full_text = ''
        links = []
        for el in elements:
            tr = el.get('text_run', {})
            content = tr.get('content', '')
            style = tr.get('text_element_style', {})
            link_url = style.get('link', {}).get('url', '')
            full_text += content
            if link_url:
                links.append((unquote(link_url), content))

        # 进入日报区域（兼容"AI日报汇总"和"AIGC早报汇总"两种标题）
        if 'AI日报汇总' in full_text or 'AIGC早报汇总' in full_text:
            in_daily = True
            continue
        # 退出日报区域：遇到同级或更高级的新 heading（heading3 及以上）
        if in_daily and block_key in ('heading3', 'heading2', 'heading1') and full_text.strip():
            break

        if not in_daily or not links:
            continue

        for url, link_text in links:
            # 只取 AIGC早报 链接，跳过其他（如"中文大模型基准测评"）
            if 'AIGC早报' not in link_text and 'AI日报' not in link_text:
                continue
            # 提取日期
            dm = re.search(r'(\d{8})', link_text)
            daily_date = dm.group(1) if dm else ''
            daily_items.append({
                'short_url': url,
                'link_text': link_text,
                'daily_date': daily_date,
            })

    return daily_items


def resolve_zsxq_short_to_article(short_url, zsxq_token):
    """解析知识星球短链接，获取 article_url

    Returns:
        (article_url, topic_id) or (None, None)
    """
    try:
        resp = ZSXQ_SESSION.get(short_url, allow_redirects=False, timeout=10)
        if resp.status_code != 302:
            return None, None
        location = resp.headers.get('Location', '')
        m = re.search(r'topic_id=(\d+)', location)
        if not m:
            return None, None
        topic_id = m.group(1)

        # 通过 API 获取 article_url（最多重试 5 次，指数退避，应对偶发 1059 内部错误）
        api_headers = {
            'Cookie': f'zsxq_access_token={zsxq_token}',
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://wx.zsxq.com/',
        }
        data = {}
        for attempt in range(5):
            api_resp = ZSXQ_SESSION.get(
                f'https://api.zsxq.com/v2/topics/{topic_id}',
                headers=api_headers, timeout=10
            )
            data = api_resp.json()
            if data.get('succeeded'):
                break
            if attempt == 0 and api_resp.status_code != 200:
                print(f'  星球 topic API 失败: HTTP {api_resp.status_code} {data.get("error") or data.get("info") or ""}'.strip())
            if attempt < 4:
                time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s, 8s
        if not data.get('succeeded'):
            return None, topic_id

        topic = data.get('resp_data', {}).get('topic', {})
        talk = topic.get('talk', {})
        article = talk.get('article', {})
        article_url = article.get('article_url', '')
        return article_url or None, topic_id
    except Exception as e:
        print(f'  解析日报短链接失败: {e}')
        return None, None


def resolve_daily_url(url, zsxq_token):
    """将 --daily 输入 URL 统一转为 articles.zsxq.com 直链

    支持两种格式：
    - https://articles.zsxq.com/id_xxx.html → 直接返回
    - https://t.zsxq.com/xxx → 解析短链接获取 article_url
    """
    if 'articles.zsxq.com/' in url:
        return url
    if 't.zsxq.com/' in url:
        article_url, topic_id = resolve_zsxq_short_to_article(url, zsxq_token)
        if article_url:
            return article_url
        print(f'  短链接解析失败（topic_id={topic_id}）')
        return None
    print(f'  不支持的 URL 格式: {url}')
    return None


def extract_urls_from_daily_article(article_url, zsxq_token, exclude_urls=None):
    """从日报全文网页提取 URL 列表

    Args:
        exclude_urls: 无条件排除的 URL 前缀列表（从配置读取）
    Returns:
        (list of dict, str): ([{url, link_text, has_important, is_reference}, ...], html_text)
    """
    try:
        headers = {
            'Cookie': f'zsxq_access_token={zsxq_token}',
            'User-Agent': 'Mozilla/5.0',
        }
        resp = ZSXQ_SESSION.get(article_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f'  日报网页请求失败: {resp.status_code}')
            return [], ''
    except Exception as e:
        print(f'  日报网页请求异常: {e}')
        return [], ''

    html_text = resp.text
    soup = BeautifulSoup(html_text, 'html.parser')
    results = []
    seen = set()

    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        if not href.startswith('http') or href in seen:
            continue
        # 无条件排除的 URL
        if exclude_urls and any(href.startswith(u) for u in exclude_urls):
            continue
        link_text = a_tag.get_text().strip()
        # 排除：域名匹配 且 链接文字含"日报"或"周报"
        if any(d in href for d in DAILY_EXCLUDE_DOMAINS):
            if '日报' in link_text or '周报' in link_text:
                continue
        seen.add(href)

        # 从 <p> 父元素获取上下文
        p_tag = a_tag.find_parent('p')
        ctx = p_tag.get_text() if p_tag else ''

        # 判断前缀标记
        prefix = ctx.split(link_text)[0] if link_text and link_text in ctx else ctx
        has_important = '【重要】' in prefix
        is_ref = 'hanniman评注' in prefix or '隐身阅读' in prefix

        results.append({
            'url': href,
            'link_text': link_text,
            'has_important': has_important,
            'is_reference': is_ref,
        })

    return results, html_text


def _write_url_fail_log(doc_url, fail_items):
    """写日报 URL 失败日志，每个周报一个块，块间空两行"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(URL_FAIL_LOG, 'a', encoding='utf-8') as f:
        f.write(f'[{now}] 周报: {doc_url}\n')
        if not fail_items:
            f.write('  正确执行\n')
        else:
            for item in fail_items:
                f.write(f'  无法获取全文链接，跳过  [{item["link_text"]}]  {item["short_url"]}\n')
        f.write('\n\n')


def process_daily_phase(client, parser, parsed_rows, url_items,
                        doc_token, doc_url, weekly_time, zsxq_token,
                        exclude_urls=None):
    """阶段三（日报核对）：提取日报 URL，与周报结果交叉比对

    修改 parsed_rows（就地更新），返回新增的 url_items 和 error_rows。
    """
    print('\n  阶段三：日报核对', flush=True)

    # 3.1 提取日报链接
    daily_links = extract_daily_links_from_doc(client, doc_token)
    print(f'  日报链接: {len(daily_links)} 篇', flush=True)
    if not daily_links:
        print('  无日报链接，跳过')
        _write_url_fail_log(doc_url, [])
        return [], []

    # 建立周报 URL 索引 (normalized_url -> row index)
    # 同时索引原始 URL 和解析后的 URL，确保日报核对时能匹配
    weekly_url_map = {}
    for idx, row in enumerate(parsed_rows):
        weekly_url_map[normalize_url(row['链接'])] = idx
    for idx, item in enumerate(url_items):
        weekly_url_map[normalize_url(item['url'])] = idx

    new_url_items = []
    new_error_rows = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    daily_csv_file = os.path.join(AIPM_DIR, 'aipm_daily_urls.csv')
    daily_url_records = []  # 用于写临时文件
    fail_items = []         # 无法获取全文链接的日报

    # 3.2 逐篇日报处理
    for di, daily in enumerate(daily_links, 1):
        short_url = daily['short_url']
        daily_date = daily['daily_date']
        print(f'\n  [日报 {di}/{len(daily_links)}] '
              f'{daily["link_text"]}', flush=True)

        # 解析短链接获取全文 URL
        article_url, topic_id = resolve_zsxq_short_to_article(
            short_url, zsxq_token)
        if not article_url:
            print(f'    无法获取全文链接，跳过')
            fail_items.append(daily)
            continue
        print(f'    全文: {article_url}')

        # 提取日报中的 URL
        daily_urls, _ = extract_urls_from_daily_article(article_url, zsxq_token, exclude_urls)
        print(f'    提取到 {len(daily_urls)} 个 URL')

        for du in daily_urls:
            url = du['url']
            daily_url_records.append({
                'daily_date': daily_date,
                'url': url,
                'link_text': du['link_text'],
            })

            # 先用原始 URL 匹配周报索引
            norm_url = normalize_url(url)

            if norm_url in weekly_url_map:
                # ── 已存在于周报 ──
                row_idx = weekly_url_map[norm_url]
                row = parsed_rows[row_idx]
                old_sel = row['精选合集']

                if old_sel == 'PA-周++':
                    row['精选合集'] = 'PA-日周++'
                    print(f'    [周报已有] {url[:60]} '
                          f'升级→PA-日周++')
                elif old_sel == 'PB-周':
                    row['精选合集'] = 'PB-日周'
                    print(f'    [周报已有] {url[:60]} '
                          f'升级→PB-日周')
                else:
                    print(f'    [周报已有] {url[:60]} '
                          f'保持 {old_sel}')

                # 日期补全
                if not row['日期'] and daily_date:
                    row['日期'] = daily_date
                    row['星期'] = '\\'
                    print(f'    日期补全→{daily_date}')
            else:
                # ── 新 URL ──
                print(f'    [新URL] {url[:60]}')

                # 解析文章
                article = parser.parse_url(url, link_text=du['link_text'])
                final_url = article['url']
                final_norm = normalize_url(final_url)

                # 解析后 URL 可能变化（重定向等），再次检查是否已存在
                if final_norm in weekly_url_map:
                    row_idx = weekly_url_map[final_norm]
                    row = parsed_rows[row_idx]
                    old_sel = row['精选合集']
                    if old_sel == 'PA-周++':
                        row['精选合集'] = 'PA-日周++'
                    elif old_sel == 'PB-周':
                        row['精选合集'] = 'PB-日周'
                    if not row['日期'] and daily_date:
                        row['日期'] = daily_date
                        row['星期'] = '\\'
                    continue

                title = article['title']
                if not title:
                    title = f'（{du["link_text"]}）'

                # 精选合集
                if du['is_reference']:
                    selection = 'PR_引用'
                elif du['has_important']:
                    selection = 'PA-日++'
                else:
                    selection = 'PB-日'

                # 日期补全
                pub_date = article['publish_date']
                weekday = article['weekday']
                if not pub_date and daily_date:
                    pub_date = daily_date
                    weekday = '\\'

                new_row = {
                    '链接': final_url,
                    '标题': title,
                    '日期': pub_date,
                    '星期': weekday,
                    '来源': article['source'],
                    '精选合集': selection,
                    '周报时间': weekly_time,
                    '异常信息': article.get('error_info', ''),
                }
                parsed_rows.append(new_row)
                new_idx = len(parsed_rows) - 1
                weekly_url_map[final_norm] = new_idx
                weekly_url_map[normalize_url(url)] = new_idx

                # 收集错误
                if article.get('error_info'):
                    new_error_rows.append({
                        **new_row,
                        '原始URL': url,
                        '记录时间': now_str,
                    })

                # 构建 url_item 用于统计
                new_url_items.append({
                    'url': url,
                    'link_text': du['link_text'],
                    'full_text': '',
                    'has_important': du['has_important'],
                    'is_reference': du['is_reference'],
                    'weekly_time': weekly_time,
                })

                time.sleep(1)

        time.sleep(1)

    # 3.3 对失败日报做一轮集中补重试（间隔更长，API 冷却后成功率更高）
    if fail_items:
        print(f'\n  --- 补重试失败日报 ({len(fail_items)} 篇) ---')
        time.sleep(10)
        still_failed = []
        for fi, daily in enumerate(fail_items, 1):
            short_url = daily['short_url']
            daily_date = daily['daily_date']
            print(f'\n  [补重试 {fi}/{len(fail_items)}] '
                  f'{daily["link_text"]}', flush=True)

            article_url, topic_id = resolve_zsxq_short_to_article(
                short_url, zsxq_token)
            if not article_url:
                print(f'    补重试仍失败，跳过')
                still_failed.append(daily)
                continue
            print(f'    全文: {article_url}')

            daily_urls, _ = extract_urls_from_daily_article(article_url, zsxq_token, exclude_urls)
            print(f'    提取到 {len(daily_urls)} 个 URL')

            for du in daily_urls:
                url = du['url']
                daily_url_records.append({
                    'daily_date': daily_date,
                    'url': url,
                    'link_text': du['link_text'],
                })
                norm_url = normalize_url(url)
                if norm_url in weekly_url_map:
                    row_idx = weekly_url_map[norm_url]
                    row = parsed_rows[row_idx]
                    old_sel = row['精选合集']
                    if old_sel == 'PA-周++':
                        row['精选合集'] = 'PA-日周++'
                        print(f'    [周报已有] {url[:60]} '
                              f'升级→PA-日周++')
                    elif old_sel == 'PB-周':
                        row['精选合集'] = 'PB-日周'
                        print(f'    [周报已有] {url[:60]} '
                              f'升级→PB-日周')
                    else:
                        print(f'    [周报已有] {url[:60]} '
                              f'保持 {old_sel}')
                    if not row['日期'] and daily_date:
                        row['日期'] = daily_date
                        row['星期'] = '\\'
                        print(f'    日期补全→{daily_date}')
                else:
                    print(f'    [新URL] {url[:60]}')
                    article = parser.parse_url(url, link_text=du['link_text'])
                    final_url = article['url']
                    final_norm = normalize_url(final_url)
                    if final_norm in weekly_url_map:
                        row_idx = weekly_url_map[final_norm]
                        row = parsed_rows[row_idx]
                        if row['精选合集'] == 'PA-周++':
                            row['精选合集'] = 'PA-日周++'
                        elif row['精选合集'] == 'PB-周':
                            row['精选合集'] = 'PB-日周'
                        if not row['日期'] and daily_date:
                            row['日期'] = daily_date
                            row['星期'] = '\\'
                        continue
                    title = article['title']
                    if not title:
                        title = f'（{du["link_text"]}）'
                    if du['is_reference']:
                        selection = 'PR_引用'
                    elif du['has_important']:
                        selection = 'PA-日++'
                    else:
                        selection = 'PB-日'
                    pub_date = article['publish_date']
                    weekday = article['weekday']
                    if not pub_date and daily_date:
                        pub_date = daily_date
                        weekday = '\\'
                    new_row = {
                        '链接': final_url,
                        '标题': title,
                        '日期': pub_date,
                        '星期': weekday,
                        '来源': article['source'],
                        '精选合集': selection,
                        '周报时间': weekly_time,
                        '异常信息': article.get('error_info', ''),
                    }
                    parsed_rows.append(new_row)
                    new_idx = len(parsed_rows) - 1
                    weekly_url_map[final_norm] = new_idx
                    weekly_url_map[normalize_url(url)] = new_idx
                    if article.get('error_info'):
                        new_error_rows.append({
                            **new_row,
                            '原始URL': url,
                            '记录时间': now_str,
                        })
                    new_url_items.append({
                        'url': url,
                        'link_text': du['link_text'],
                        'full_text': '',
                        'has_important': du['has_important'],
                        'is_reference': du['is_reference'],
                        'weekly_time': weekly_time,
                    })
                    time.sleep(1)
            time.sleep(1)
        fail_items = still_failed

    # 写日报 URL 临时文件
    if daily_url_records:
        headers_csv = ['日报日期', 'URL', '链接文字']
        with open(daily_csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers_csv)
            writer.writeheader()
            for rec in daily_url_records:
                writer.writerow({
                    '日报日期': rec['daily_date'],
                    'URL': rec['url'],
                    '链接文字': rec['link_text'],
                })
        print(f'\n  日报 URL 临时文件: {daily_csv_file} '
              f'({len(daily_url_records)} 条)')

    print(f'\n  日报核对完成: 新增 {len(new_url_items)} 条 URL')
    _write_url_fail_log(doc_url, fail_items)
    return new_url_items, new_error_rows


def normalize_github_raw_url(url):
    """将 raw.githubusercontent.com URL 转为 github.com/blob 格式"""
    m = re.match(
        r'https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/refs/heads/(.+)',
        url
    )
    if m:
        return f'https://github.com/{m.group(1)}/{m.group(2)}/blob/{m.group(3)}'
    return url


def determine_source_for_special(url):
    """为 article_parser 未覆盖的 URL 判定来源"""
    if 'github.com/' in url:
        return 'Web-GitHub'
    if 'arxiv.org/pdf/' in url or 'arxiv.org/abs/' in url:
        return 'Web-arxiv'
    if '.feishu.cn/minutes/' in url:
        return '飞书会议纪要'
    # 官方网站
    official_domains = [
        'labs.google', 'figma.com/make', 'bot.n.cn', 'notebooklm.google',
        'introml.mit.edu', 'menugen.app', 'cap.so', 'github.io/',
    ]
    for domain in official_domains:
        if domain in url:
            return 'Web官方'
    return ''


def _parse_single_url(url, item, parser, client=None):
    """解析单个 URL，返回 (final_url, article) 元组"""
    link_text = item['link_text']

    # GitHub raw URL 转换
    url = normalize_github_raw_url(url)

    # 统一调用 article_parser 解析
    article = parser.parse_url(url, link_text=link_text)

    # 补充来源（article_parser 未覆盖的）
    if not article['source'] or article.get('error_info', '').startswith('未提取来源'):
        special_source = determine_source_for_special(article['url'])
        if special_source:
            article['source'] = special_source

    return article.get('url', url), article


def parse_urls_phase2(url_items, parser, client=None):
    """阶段二：解析每个 URL 的文章信息

    Returns:
        (parsed_rows, error_rows)
    """
    parsed_rows = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # --- 第一轮解析 ---
    retry_indices = []  # 需要重试的索引

    for idx, item in enumerate(url_items):
        url = item['url']
        print(f'\n[{idx+1}/{len(url_items)}] {url[:80]}')

        final_url, article = _parse_single_url(url, item, parser, client)

        # 标题回退
        title = article['title']
        if not title:
            title = f'（{item["link_text"]}）'

        selection = 'PA-周++' if item['has_important'] else \
                    'PR_引用' if item.get('is_reference') else 'PB-周'

        row = {
            '链接': article['url'],
            '标题': title,
            '日期': article['publish_date'],
            '星期': article['weekday'],
            '来源': article['source'],
            '精选合集': selection,
            '周报时间': item['weekly_time'],
            '异常信息': article.get('error_info', ''),
        }
        parsed_rows.append(row)

        print(f'  标题: {title[:60]}')
        print(f'  来源: {article["source"]}  日期: {article["publish_date"]}')

        # 有异常或标题回退，加入重试列表
        err = article.get('error_info', '')
        url_lower = url.lower()
        need_retry = bool(err and '未配置' not in err)
        if not need_retry and title.startswith('（') and title.endswith('）'):
            need_retry = True
        if need_retry and not any(d in url_lower for d in SKIP_RETRY_DOMAINS):
            retry_indices.append(idx)

        time.sleep(1)

    # --- 第二轮重试 ---
    if retry_indices:
        print(f'\n{"=" * 40}')
        print(f'重试解析失败的 URL ({len(retry_indices)} 条)')
        print('=' * 40)
        time.sleep(3)

        for idx in retry_indices:
            item = url_items[idx]
            url = item['url']
            old_row = parsed_rows[idx]
            print(f'\n[重试 {idx+1}] {url[:80]}')

            final_url, article = _parse_single_url(url, item, parser, client)

            # 判断重试是否有改善
            old_err = old_row.get('异常信息', '')
            new_err = article.get('error_info', '')
            old_title_fallback = old_row['标题'].startswith('（') and old_row['标题'].endswith('）')
            new_has_title = bool(article['title'])
            improved = (not new_err or len(new_err) < len(old_err)) \
                       or (old_title_fallback and new_has_title)

            if improved:
                title = article['title']
                if not title:
                    title = f'（{item["link_text"]}）'
                selection = 'PA-周++' if item['has_important'] else \
                            'PR_引用' if item.get('is_reference') else 'PB-周'
                parsed_rows[idx] = {
                    '链接': article['url'],
                    '标题': title,
                    '日期': article['publish_date'],
                    '星期': article['weekday'],
                    '来源': article['source'],
                    '精选合集': selection,
                    '周报时间': item['weekly_time'],
                    '异常信息': article.get('error_info', ''),
                }
                print(f'  重试成功: {title[:60]}')
            else:
                print(f'  重试仍失败: {article.get("error_info", "")[:60]}')

            time.sleep(1)

    print()  # 重试结束分隔

    # --- 收集最终错误 ---
    error_rows = []
    for idx, row in enumerate(parsed_rows):
        if row.get('异常信息'):
            error_rows.append({
                **row,
                '原始URL': url_items[idx]['url'],
                '记录时间': now_str,
            })

    return parsed_rows, error_rows


def write_urls_csv(url_items, filepath):
    """将阶段一提取的 URL 写入临时 CSV"""
    headers = ['序号', 'URL', '链接文字', '是否重要', '周报时间']
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for i, item in enumerate(url_items, 1):
            writer.writerow({
                '序号': i,
                'URL': item['url'],
                '链接文字': item['link_text'],
                '是否重要': '是' if item['has_important'] else '否',
                '周报时间': item['weekly_time'],
            })
    print(f'  已写入 {filepath} ({len(url_items)} 条)')


def write_parsed_csv(parsed_rows, filepath):
    """将阶段二解析结果写入临时 CSV"""
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=PARSED_HEADERS)
        writer.writeheader()
        for row in parsed_rows:
            writer.writerow({h: row.get(h, '') for h in PARSED_HEADERS})
    print(f'  已写入 {filepath} ({len(parsed_rows)} 条)')


def write_error_log(error_rows, filepath):
    """将解析错误追加写入日志 CSV（UTF-8 BOM，Excel 友好）"""
    if not error_rows:
        return
    headers = ['链接', '标题', '日期', '来源', '异常信息', '原始URL', '记录时间', '周报时间']
    need_header = not os.path.exists(filepath) or os.path.getsize(filepath) == 0
    if need_header:
        # 新建：utf-8-sig 自动写入 BOM
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in error_rows:
                writer.writerow({h: row.get(h, '') for h in headers})
    else:
        # 追加：用 utf-8，不重复写 BOM
        with open(filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            for row in error_rows:
                writer.writerow({h: row.get(h, '') for h in headers})
    print(f'  错误日志已写入 {filepath} ({len(error_rows)} 条)')


def compare_with_sample(client, parsed_rows, config):
    """与 sample 表格对照"""
    sample_cfg = config.get('weekly_report', {}).get('sample_bitable', {})
    if not sample_cfg.get('app_token'):
        print('  未配置 sample 表格，跳过对照')
        return

    print('\n正在读取 sample 表格...')
    records = client.get_bitable_records(
        sample_cfg['app_token'], sample_cfg['table_id'],
        ['链接', '标题', '来源', '日期', '星期', '精选合集', '周报时间']
    )
    sample_map = {}
    for rec in records:
        url = rec.get('链接', '')
        if url:
            sample_map[url] = rec

    print(f'  sample 记录: {len(sample_map)} 条')
    print(f'  解析结果: {len(parsed_rows)} 条')

    # 对照
    parsed_urls = {r['链接'] for r in parsed_rows}
    sample_urls = set(sample_map.keys())

    missing = sample_urls - parsed_urls
    extra = parsed_urls - sample_urls

    if missing:
        print(f'\n  sample 中有但解析结果中缺少 ({len(missing)} 条):')
        for url in missing:
            title = sample_map[url].get('标题', '')
            print(f'    {title[:50]}  {url[:80]}')

    if extra:
        print(f'\n  解析结果中有但 sample 中没有 ({len(extra)} 条):')
        for url in extra:
            print(f'    {url[:80]}')

    if not missing and not extra:
        print('  URL 完全匹配!')

    return sample_map


def write_to_bitable(client, parsed_rows, config):
    """将解析结果写入飞书多维表格

    Returns:
        (bool, list): (是否全部成功, [{'url':..., 'record_id':...}, ...])
    """
    bt_cfg = config.get('weekly_report', {}).get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    if not app_token or not table_id:
        print('  多维表格配置缺失，跳过写入')
        return False, []

    # 构建待写入记录（批次内去重）
    new_records = []
    new_urls = []
    seen_urls = set()
    for row in parsed_rows:
        url = row['链接']
        if url in seen_urls:
            continue
        seen_urls.add(url)
        fields = {
            '链接': url,
            '标题': row['标题'],
            '来源': row['来源'],
            '精选合集': row['精选合集'],
            '周报时间': row['周报时间'],
        }
        if row['日期']:
            fields['日期'] = int(row['日期']) if row['日期'].isdigit() else row['日期']
        if row['星期']:
            fields['星期'] = row['星期']
        new_records.append(fields)
        new_urls.append(url)

    if not new_records:
        print('  无新记录需要写入')
        return True, []

    print(f'  准备写入 {len(new_records)} 条新记录...')
    result = client.batch_add_bitable_records(app_token, table_id, new_records)
    ok = result['success']
    fail = result['failed']
    print(f'  写入完成: 成功 {ok}, 失败 {fail}')

    created = result.get('records', [])
    cache_entries = []
    for i, rec in enumerate(created):
        url = new_urls[i] if i < len(new_urls) else ''
        rid = rec.get('record_id', '') if isinstance(rec, dict) else ''
        if url:
            cache_entries.append({'url': url, 'record_id': rid})

    return fail == 0, cache_entries


def fetch_group_topics_page(group_id, zsxq_token, end_time=None, count=20, retries=3):
    """获取群组帖子列表（单页），含重试"""
    headers = {
        'Cookie': f'zsxq_access_token={zsxq_token}',
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://wx.zsxq.com/',
    }
    url = f'https://api.zsxq.com/v2/groups/{group_id}/topics?scope=all&count={count}'
    if end_time:
        url += f'&end_time={end_time.replace("+", "%2B")}'
    for attempt in range(1, retries + 1):
        try:
            resp = ZSXQ_SESSION.get(url, headers=headers, timeout=15)
            data = resp.json()
            if data.get('succeeded'):
                if attempt > 1:
                    print(f'  星球帖子列表重试成功（尝试 {attempt}/{retries}）')
                return data.get('resp_data', {}).get('topics', [])
            msg = (data.get("msg") or data.get("message") or
                   data.get("error") or data.get("info") or "接口返回 succeeded=false")
            code = data.get('code')
            if code is not None:
                msg = f'{msg}（code={code}）'
            if resp.status_code in (401, 403):
                raise ZsxqAuthError(f'HTTP {resp.status_code} {msg}'.strip())
            reason = f'HTTP {resp.status_code} {msg}'.strip()
        except ZsxqAuthError:
            raise
        except Exception as e:
            reason = f'请求异常：{e}'
        if attempt < retries:
            wait_seconds = 2 * attempt
            print(f'  星球帖子列表获取失败（尝试 {attempt}/{retries}）：'
                  f'{reason}；{wait_seconds} 秒后重试')
            time.sleep(wait_seconds)
        else:
            print(f'  星球帖子列表获取失败（尝试 {attempt}/{retries}）：'
                  f'{reason}；已停止重试')
    return None


def _extract_daily_date_from_title(title):
    """从星球帖子标题提取日报日期，兼容 AI日报 / AIGC早报 的常见写法。"""
    m = re.search(r'(?:AI日报|AIGC早报)[_：:\-\s]*(\d{8})', title or '')
    if m:
        return m.group(1)
    return ''


def _parse_zsxq_create_ts(time_str):
    """解析知识星球 create_time 为 Unix 时间戳（秒）。"""
    if not time_str:
        return 0
    time_str = re.sub(r'(\+\d{2})(\d{2})$', r'\1:\2', time_str)
    try:
        return int(datetime.fromisoformat(time_str).timestamp())
    except Exception:
        return 0


def _parse_last_processed_marker(raw_value):
    """兼容旧 YYYYMMDD 和新 Unix 时间戳两种 last_processed_date 格式。"""
    text = str(raw_value or '').strip()
    if not text:
        return 0, ''
    if text.isdigit() and len(text) >= 10:
        ts = int(text)
        return ts, datetime.fromtimestamp(ts).strftime('%Y%m%d')
    if text.isdigit() and len(text) == 8:
        dt = datetime.strptime(text, '%Y%m%d') + timedelta(days=1, seconds=-1)
        return int(dt.timestamp()), text
    return 0, ''


def find_daily_articles_since(group_id, zsxq_token, since_ts_exclusive=0, since_date=''):
    """查找指定日期之后的所有日报文章

    Args:
        since_ts_exclusive: Unix 时间戳（秒），查找此时间之后的日报
        since_date: 兼容旧逻辑的 YYYYMMDD 下界
    Returns:
        list of (date_str, article_url, topic_ts)，按日期升序
    """
    found = []
    seen_dates = set()
    end_time = None
    reached_boundary = False

    for page in range(1, 21):
        try:
            topics = fetch_group_topics_page(group_id, zsxq_token, end_time)
        except ZsxqAuthError as e:
            print(f'  知识星球认证失败: {e}')
            print('  请更新 ~/.config/secrets/gtokens.yaml 中的 '
                  'zsxq.access_token 后重试')
            return None
        if topics is None:
            print(f'  第 {page} 页最终获取失败，停止查找日报')
            return None
        if not topics:
            break

        for t in topics:
            talk = t.get('talk', {})
            article_title = talk.get('article', {}).get('title', '')
            article_url = talk.get('article', {}).get('article_url', '')
            topic_ts = _parse_zsxq_create_ts(t.get('create_time', ''))
            daily_date = _extract_daily_date_from_title(article_title)
            if not daily_date:
                continue
            if since_ts_exclusive and topic_ts and topic_ts <= since_ts_exclusive:
                reached_boundary = True
                break
            if since_date and daily_date <= since_date:
                if since_ts_exclusive:
                    continue
                reached_boundary = True
                break
            if article_url and daily_date not in seen_dates:
                seen_dates.add(daily_date)
                found.append((daily_date, article_url, topic_ts))

        if reached_boundary:
            break
        end_time = topics[-1]['create_time']
        time.sleep(1)

    found.sort(key=lambda x: (x[0], x[2]))

    # 检查日期连续性
    if found:
        start = datetime.strptime(found[0][0], '%Y%m%d').date()
        end = datetime.strptime(found[-1][0], '%Y%m%d').date()
        found_dates = {item[0] for item in found}
        d = start
        while d <= end:
            ds = d.strftime('%Y%m%d')
            if ds not in found_dates:
                print(f'  跳过 {ds}，未找到对应日报')
            d += timedelta(days=1)

    print(f'  日报查找完成：找到 {len(found)} 篇')
    return found


def get_next_saturday_weekly_time(ref_date=None):
    """计算指定日期所属的周六（含当日），格式如 '260118周'

    ref_date: YYYYMMDD 字符串，默认使用当前日期
    """
    if ref_date:
        d = datetime.strptime(ref_date, '%Y%m%d').date()
    else:
        d = datetime.now().date()
    weekday = d.weekday()  # 0=Mon ... 5=Sat 6=Sun
    if weekday <= 5:
        days_ahead = 5 - weekday
    else:
        days_ahead = 6  # 周日 → 下周六
    sat = d + timedelta(days=days_ahead)
    return sat.strftime('%y%m%d') + '周'


def process_daily_standalone(client, parser, config, daily_url, zsxq_token,
                             bitable_cache=None):
    """--daily 模式：从日报文档提取 URL → 解析 → 写入多维表格"""
    print(f'\n输入 URL: {daily_url}')

    # 1. 解析日报 URL → 获取 article_url
    article_url = resolve_daily_url(daily_url, zsxq_token)
    if not article_url:
        print('无法获取日报文章链接，退出')
        return False
    if article_url != daily_url:
        print(f'文章直链: {article_url}')

    # 2. 提取日报中的外部链接
    wr_cfg = config.get('weekly_report', {})
    exclude_urls = wr_cfg.get('daily_exclude_urls', [])
    daily_urls, html_text = extract_urls_from_daily_article(article_url, zsxq_token, exclude_urls)
    print(f'\n阶段一：提取到 {len(daily_urls)} 个 URL')
    if not daily_urls:
        print('日报中未提取到外部链接')
        return False

    # 提取日报日期（从页面标题 _YYYYMMDD 或正文 YYYY年MM月DD日）
    daily_date = ''
    m = re.search(r'_(\d{8})', html_text)
    if m:
        daily_date = m.group(1)
    else:
        m = re.search(r'(\d{4})\u5e74(\d{2})\u6708(\d{2})\u65e5', html_text)
        if m:
            daily_date = f'{m.group(1)}{m.group(2)}{m.group(3)}'
    if daily_date:
        print(f'日报日期: {daily_date}')

    weekly_time = get_next_saturday_weekly_time(daily_date)
    print(f'周报时间: {weekly_time}')

    # 3. 解析每个 URL
    print(f'\n阶段二：解析 URL', flush=True)
    parsed_rows = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    retry_indices = []

    for idx, du in enumerate(daily_urls):
        url = du['url']
        print(f'\n[{idx+1}/{len(daily_urls)}] {url[:80]}')

        item = {
            'url': url,
            'link_text': du['link_text'],
            'full_text': '',
            'has_important': du['has_important'],
            'is_reference': du['is_reference'],
            'weekly_time': weekly_time,
        }
        final_url, article = _parse_single_url(url, item, parser, client)

        title = article['title']
        if not title:
            title = f'（{du["link_text"]}）'

        if du['is_reference']:
            selection = 'PR_引用'
        elif du['has_important']:
            selection = 'PA-日++'
        else:
            selection = 'PB-日'

        # 日期补全：解析器未获取到日期时，用日报日期
        pub_date = article['publish_date']
        weekday = article['weekday']
        if not pub_date and daily_date:
            pub_date = daily_date
            weekday = '\\'

        row = {
            '链接': article['url'],
            '标题': title,
            '日期': pub_date,
            '星期': weekday,
            '来源': article['source'],
            '精选合集': selection,
            '周报时间': weekly_time,
            '异常信息': article.get('error_info', ''),
        }
        parsed_rows.append(row)
        print(f'  标题: {title[:60]}')
        print(f'  来源: {article["source"]}  日期: {pub_date}')

        err = article.get('error_info', '')
        url_lower = url.lower()
        # 有异常，或标题回退到 link_text（未解析出真实标题），加入重试
        need_retry = bool(err and '未配置' not in err)
        if not need_retry and title.startswith('（') and title.endswith('）'):
            need_retry = True
        if need_retry and not any(d in url_lower for d in SKIP_RETRY_DOMAINS):
            retry_indices.append(idx)

        time.sleep(1)

    # 4. 重试失败的 URL
    if retry_indices:
        print(f'\n{"=" * 40}')
        print(f'重试解析失败的 URL ({len(retry_indices)} 条)')
        print('=' * 40)
        time.sleep(3)

        for idx in retry_indices:
            du = daily_urls[idx]
            url = du['url']
            old_row = parsed_rows[idx]
            print(f'\n[重试 {idx+1}] {url[:80]}')

            item = {
                'url': url, 'link_text': du['link_text'],
                'full_text': '', 'has_important': du['has_important'],
                'is_reference': du['is_reference'], 'weekly_time': weekly_time,
            }
            final_url, article = _parse_single_url(url, item, parser, client)

            # 判断重试是否有改善：error_info 减少，或获取到了真实标题
            old_err = old_row.get('异常信息', '')
            new_err = article.get('error_info', '')
            old_title_fallback = old_row['标题'].startswith('（') and old_row['标题'].endswith('）')
            new_has_title = bool(article['title'])
            improved = (not new_err or len(new_err) < len(old_err)) \
                       or (old_title_fallback and new_has_title)

            if improved:
                title = article['title']
                if not title:
                    title = f'（{du["link_text"]}）'
                if du['is_reference']:
                    selection = 'PR_引用'
                elif du['has_important']:
                    selection = 'PA-日++'
                else:
                    selection = 'PB-日'
                pub_date = article['publish_date']
                weekday = article['weekday']
                if not pub_date and daily_date:
                    pub_date = daily_date
                    weekday = '\\'
                parsed_rows[idx] = {
                    '链接': article['url'],
                    '标题': title,
                    '日期': pub_date,
                    '星期': weekday,
                    '来源': article['source'],
                    '精选合集': selection,
                    '周报时间': weekly_time,
                    '异常信息': article.get('error_info', ''),
                }
                print(f'  重试成功: {title[:60]}')
            else:
                print(f'  重试仍失败: {article.get("error_info", "")[:60]}')

            time.sleep(1)

    # 5. 错误日志
    error_rows = []
    for idx, row in enumerate(parsed_rows):
        if row.get('异常信息'):
            error_rows.append({
                **row,
                '原始URL': daily_urls[idx]['url'],
                '记录时间': now_str,
            })
    if error_rows:
        write_error_log(error_rows, ERROR_LOG_FILE)

    # 6. 写入多维表格
    print(f'\n阶段三：写入多维表格', flush=True)
    success, cache_entries = write_to_bitable(client, parsed_rows, config)
    if bitable_cache and cache_entries:
        bitable_cache.append(cache_entries)
        print(f'  追加 {len(cache_entries)} 条到缓存')

    # 汇总
    print(f'\n{"=" * 40}')
    print(f'日报处理完成')
    print(f'  提取: {len(daily_urls)} 条  解析: {len(parsed_rows)} 条'
          f'  异常: {len(error_rows)} 条')
    print('=' * 40)
    return success


def process_daily_update(client, parser, config, credentials):
    """--update 模式：自动查找并处理 last_processed_date 之后的日报"""
    wr_cfg = config.get('weekly_report', {})
    group_url = wr_cfg.get('zsxq_group_url', '')
    last_marker = wr_cfg.get('last_processed_date', '')
    last_ts, last_date = _parse_last_processed_marker(last_marker)
    zsxq_token = credentials['zsxq_token']

    if not group_url:
        print('未配置 zsxq_group_url，退出')
        return
    if not last_marker:
        print('未配置 last_processed_date，退出')
        return
    if not zsxq_token:
        print('未配置 zsxq token，退出')
        return

    # 从 URL 提取 group_id
    m = re.search(r'/group/(\d+)', group_url)
    if not m:
        print(f'无法从 URL 提取 group_id: {group_url}')
        return
    group_id = m.group(1)

    print(f'上次处理日期: {last_date or last_marker}')
    print(f'知识星球群组: {group_id}')
    print(f'正在查找新日报...', flush=True)

    daily_articles = find_daily_articles_since(
        group_id, zsxq_token, since_ts_exclusive=last_ts, since_date=last_date)
    if daily_articles is None:
        print('日报查找失败，未更新 last_processed_date')
        print('last_processed_date 未更新，原因：日报列表抓取失败')
        return
    if not daily_articles:
        print('没有新的日报需要处理')
        print('last_processed_date 未更新，原因：未发现新日报')
        return

    print(f'\n找到 {len(daily_articles)} 篇待处理日报:')
    for date_str, url, _topic_ts in daily_articles:
        print(f'  {date_str}  {url}')

    # 初始化缓存
    bt_cfg = wr_cfg.get('target_bitable', {})
    bt_table_id = bt_cfg.get('table_id', '')
    bitable_cache = None
    if bt_table_id:
        bitable_cache = BitableUrlCache(bt_table_id, DATA_DIR)
        bitable_cache.load()

    # 逐篇处理
    last_success_date = last_date
    last_success_ts = last_ts
    success_count = 0
    failed_count = 0
    for i, (date_str, article_url, topic_ts) in enumerate(daily_articles, 1):
        print(f'\n{"=" * 60}')
        print(f'[{i}/{len(daily_articles)}] AI日报_{date_str}')
        print('=' * 60)
        success = process_daily_standalone(
            client, parser, config, article_url, zsxq_token, bitable_cache)
        if success:
            success_count += 1
            last_success_date = date_str
            if topic_ts:
                last_success_ts = topic_ts
        else:
            failed_count += 1
            print(f'  未推进 last_processed_date，{date_str} 写入未完成')

    # 更新 last_processed_date
    if last_success_ts != last_ts:
        config_path = os.path.join(PROJECT_ROOT, 'cfg', 'config.yaml')
        set_config_value_preserve_comments(
            config_path,
            ['weekly_report', 'last_processed_date'],
            last_success_ts,
            comment=format_unix_ts_comment(last_success_ts),
        )
        print(f'\nlast_processed_date 已更新: {last_marker} → {last_success_ts} '
              f'({format_unix_ts_comment(last_success_ts)})')
    else:
        print(f'\nlast_processed_date 未更新，原因：本次没有日报处理成功')

    print(f'本次日报处理完成：待处理 {len(daily_articles)}，'
          f'成功 {success_count}，失败 {failed_count}')


def _normalize_url(url):
    """规范化 URL 用于匹配（去末尾斜杠、统一 https）"""
    url = url.strip().rstrip('/')
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    return url


def process_weekly(client, parser, config, wiki_url):
    """--weekly 模式：基于周报 wiki 完善多维表格中的周报内容"""
    # 提取 doc_token
    m = re.search(r'/wiki/([A-Za-z0-9]+)', wiki_url)
    if not m:
        print(f'无法从 URL 提取 doc_token: {wiki_url}')
        return
    doc_token = m.group(1)

    # 阶段一：提取周报 URL
    print('\n阶段一：提取周报 URL', flush=True)
    list_wiki = extract_urls_from_doc(client, doc_token)
    if not list_wiki:
        print('  未提取到 URL，退出')
        return
    weekly_time = list_wiki[0]['weekly_time']
    print(f'  提取到 {len(list_wiki)} 个 URL，周报时间: {weekly_time}')

    # 阶段二：搜索多维表格已有记录
    print('\n阶段二：搜索多维表格已有记录', flush=True)
    bt_cfg = config.get('weekly_report', {}).get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    if not app_token or not table_id:
        print('  多维表格配置缺失，退出')
        return

    filter_str = f'CurrentValue.[周报时间]= "{weekly_time}"'
    records = client.search_bitable_records(
        app_token, table_id, ['链接', '精选合集', '周报时间'], filter_str)
    print(f'  找到 {len(records)} 条已有记录')

    record_map = {}
    for rec in records:
        url_key = _normalize_url(rec.get('链接', ''))
        if url_key and url_key not in record_map:
            record_map[url_key] = {
                'record_id': rec['_record_id'],
                '精选合集': rec.get('精选合集', ''),
            }

    # 阶段三：分类
    print('\n阶段三：分类对比', flush=True)
    updates = []
    new_items = []

    for item in list_wiki:
        url_key = _normalize_url(item['url'])
        matched = record_map.get(url_key)

        if item['has_important']:
            if matched:
                updates.append({
                    'record_id': matched['record_id'],
                    'new_selection': 'PA-日周++',
                })
            else:
                item['_selection'] = 'PA-周++'
                new_items.append(item)
        else:
            if matched:
                old_val = matched['精选合集']
                if old_val != 'PA-日++':
                    updates.append({
                        'record_id': matched['record_id'],
                        'new_selection': 'PB-日周',
                    })
            else:
                item['_selection'] = 'PB-周'
                new_items.append(item)

    print(f'  待更新: {len(updates)} 条，新增: {len(new_items)} 条')

    # 阶段四：解析新增 URL
    if new_items:
        print('\n阶段四：解析新增 URL', flush=True)
        parsed_rows, error_rows = parse_urls_phase2(new_items, parser, client)
        for idx, row in enumerate(parsed_rows):
            row['精选合集'] = new_items[idx]['_selection']
            row['周报时间'] = weekly_time
        if error_rows:
            write_error_log(error_rows, ERROR_LOG_FILE)
    else:
        parsed_rows = []
        error_rows = []
        print('\n阶段四：无新增 URL 需解析')

    # 阶段五：批量更新已有记录
    if updates:
        print(f'\n阶段五：更新已有记录 ({len(updates)} 条)', flush=True)
        update_records = [
            {'record_id': u['record_id'],
             'fields': {'精选合集': u['new_selection']}}
            for u in updates
        ]
        result = client.batch_update_bitable_records(
            app_token, table_id, update_records)
        print(f'  更新完成: 成功 {result["success"]}, 失败 {result["failed"]}')
    else:
        print('\n阶段五：无需更新已有记录')

    # 初始化缓存
    bitable_cache = None
    if table_id:
        bitable_cache = BitableUrlCache(table_id, DATA_DIR)
        bitable_cache.load()

    # 阶段六：批量新增记录
    if parsed_rows:
        print(f'\n阶段六：新增记录 ({len(parsed_rows)} 条)', flush=True)
        new_records = []
        new_urls = []
        for row in parsed_rows:
            fields = {
                '链接': row['链接'],
                '标题': row['标题'],
                '来源': row['来源'],
                '精选合集': row['精选合集'],
                '周报时间': row['周报时间'],
            }
            if row['日期']:
                fields['日期'] = int(row['日期']) if row['日期'].isdigit() else row['日期']
            if row['星期']:
                fields['星期'] = row['星期']
            new_records.append(fields)
            new_urls.append(row['链接'])
        result = client.batch_add_bitable_records(app_token, table_id, new_records)
        print(f'  新增完成: 成功 {result["success"]}, 失败 {result["failed"]}')
        if bitable_cache:
            created = result.get('records', [])
            cache_entries = []
            for i, rec in enumerate(created):
                url = new_urls[i] if i < len(new_urls) else ''
                rid = rec.get('record_id', '') if isinstance(rec, dict) else ''
                if url:
                    cache_entries.append({'url': url, 'record_id': rid})
            if cache_entries:
                bitable_cache.append(cache_entries)
                print(f'  追加 {len(cache_entries)} 条到缓存')
    else:
        print('\n阶段六：无新记录需新增')

    # 汇总
    print(f'\n{"=" * 40}')
    print(f'周报完善完成')
    print(f'  更新: {len(updates)} 条  新增: {len(parsed_rows)} 条'
          f'  异常: {len(error_rows)} 条')
    print('=' * 40)


def _towiki_user_token(client):
    for key in ('auth_feishuMSG-xls', 'auth'):
        token = client.credentials.get(key, {}).get('user_access_token', '')
        if token:
            return token
    return ''


def _towiki_json_headers(client):
    token = _towiki_user_token(client)
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json; charset=utf-8',
    }


def _towiki_auth_headers(client):
    return {'Authorization': f'Bearer {_towiki_user_token(client)}'}


def _towiki_api_json(resp, label):
    if resp.status_code == 429:
        retry_after = resp.headers.get('Retry-After', '')
        raise RuntimeError(
            f'{label} 限流 429'
            f'{f" retry_after={retry_after}" if retry_after else ""}'
        )
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(
            f'{label} 响应无法解析: status={resp.status_code}, body={resp.text[:200]}'
        )
    if resp.status_code == 401:
        raise RuntimeError(
            f'{label} 认证失败: HTTP 401 {data.get("msg") or ""}'.strip())
    if resp.status_code == 403:
        raise RuntimeError(
            f'{label} 权限失败: HTTP 403 {data.get("msg") or ""}'.strip())
    if resp.status_code >= 500:
        raise RuntimeError(
            f'{label} 服务端错误: HTTP {resp.status_code} '
            f'{data.get("msg") or ""}'.strip())
    if data.get('code') != 0:
        raise RuntimeError(f'{label} 失败: {data.get("msg")} (code={data.get("code")})')
    return data.get('data', {})


def _towiki_resolve_doc_id(client, target_url):
    if '/wiki/' in target_url:
        m = re.search(r'/wiki/([A-Za-z0-9]+)', target_url)
        if not m:
            raise RuntimeError(f'无法从目标 URL 提取 wiki token: {target_url}')
        doc_id = client.get_wiki_node_info(m.group(1))
        if not doc_id:
            raise RuntimeError('目标 wiki 解析 document_id 失败')
        return doc_id
    m = re.search(r'/docx/([A-Za-z0-9]+)', target_url)
    if m:
        return m.group(1)
    raise RuntimeError(f'目标 URL 不是 wiki/docx 文档: {target_url}')


def _towiki_get_document_root(client, doc_id):
    url = f'{client.base_url}/docx/v1/documents/{doc_id}'
    resp = client._session.get(url, headers=_towiki_json_headers(client), timeout=30)
    data = _towiki_api_json(resp, '获取目标文档信息')
    document = data.get('document', {})
    return document.get('document_id') or doc_id


def _towiki_get_doc_blocks(client, doc_id):
    all_blocks = []
    page_token = None
    while True:
        params = {'page_size': 500}
        if page_token:
            params['page_token'] = page_token
        url = f'{client.base_url}/docx/v1/documents/{doc_id}/blocks'
        resp = client._session.get(
            url, headers=_towiki_json_headers(client), params=params, timeout=30
        )
        data = _towiki_api_json(resp, '读取目标文档 blocks')
        all_blocks.extend(data.get('items', []))
        page_token = data.get('page_token')
        if not data.get('has_more') and not page_token:
            break
    return all_blocks


def _towiki_clear_document(client, doc_id):
    blocks = _towiki_get_doc_blocks(client, doc_id)
    root = next((b for b in blocks if b.get('block_type') == 1), None)
    root_id = root.get('block_id') if root else _towiki_get_document_root(client, doc_id)
    child_count = len(root.get('children', [])) if root else 0
    if child_count <= 0:
        return
    url = (f'{client.base_url}/docx/v1/documents/{doc_id}/blocks/'
           f'{root_id}/children/batch_delete')
    resp = client._session.delete(
        url,
        json={'start_index': 0, 'end_index': child_count},
        headers=_towiki_json_headers(client),
        params={'document_revision_id': '-1'},
        timeout=30,
    )
    _towiki_api_json(resp, '清空目标文档')


def _towiki_text_block(text, block_type=2):
    key_map = {
        2: 'text', 3: 'heading1', 4: 'heading2', 5: 'heading3',
        6: 'heading4', 12: 'bullet', 13: 'ordered',
    }
    key = key_map.get(block_type, 'text')
    return {
        'block_type': block_type,
        key: {'elements': [{'text_run': {'content': text}}]},
    }


def _towiki_rich_text_block(elements, block_type=2):
    key_map = {
        2: 'text', 3: 'heading1', 4: 'heading2', 5: 'heading3',
        6: 'heading4', 12: 'bullet', 13: 'ordered',
    }
    key = key_map.get(block_type, 'text')
    return {'block_type': block_type, key: {'elements': elements}}


def _towiki_split_rich_elements(elements, block_type=2, limit=1800):
    blocks = []
    current = []
    current_len = 0

    def flush():
        nonlocal current, current_len
        if current:
            blocks.append(_towiki_rich_text_block(current, block_type))
            current = []
            current_len = 0

    for el in elements:
        tr = el.get('text_run', {})
        content = tr.get('content', '')
        style = tr.get('text_element_style')
        while len(content) > limit:
            room = max(1, limit - current_len)
            piece = content[:room]
            new_el = _towiki_make_text_element(piece, style)
            current.append(new_el)
            current_len += len(piece)
            content = content[room:]
            flush()
        if current_len + len(content) > limit:
            flush()
        if content:
            current.append(_towiki_make_text_element(content, style))
            current_len += len(content)
    flush()
    return blocks


def _towiki_make_text_element(text, style=None):
    el = {'text_run': {'content': text}}
    if style:
        clean_style = _towiki_sanitize_text_style(style)
        if clean_style:
            el['text_run']['text_element_style'] = clean_style
    return el


def _towiki_normalize_url(url, base_url=''):
    if not url:
        return ''
    normalized = urljoin(base_url, str(url).strip())
    parsed = urlparse(normalized)
    if parsed.scheme not in ('http', 'https'):
        return ''
    return normalized


def _towiki_normalize_css_color(value):
    if not value:
        return ''
    value = value.strip().strip('"\'')
    if value.lower() in ('inherit', 'initial', 'unset', 'transparent', 'currentcolor'):
        return ''
    if re.fullmatch(r'#[0-9a-fA-F]{3}', value):
        return '#' + ''.join(ch * 2 for ch in value[1:]).lower()
    if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return value.lower()
    m = re.match(r'rgba?\(([^)]+)\)', value, re.I)
    if m:
        parts = [p.strip() for p in m.group(1).split(',')]
        if len(parts) >= 3:
            try:
                rgb = []
                for part in parts[:3]:
                    if part.endswith('%'):
                        rgb.append(round(float(part[:-1]) * 2.55))
                    else:
                        rgb.append(int(float(part)))
                if all(0 <= c <= 255 for c in rgb):
                    return '#%02x%02x%02x' % tuple(rgb)
            except ValueError:
                return ''
    named = {
        'black': '#000000', 'white': '#ffffff', 'red': '#ff0000',
        'green': '#008000', 'blue': '#0000ff', 'yellow': '#ffff00',
        'orange': '#ffa500', 'purple': '#800080', 'gray': '#808080',
        'grey': '#808080',
    }
    return named.get(value.lower(), value)


def _towiki_hex_to_rgb(value):
    value = _towiki_normalize_css_color(value)
    if not re.fullmatch(r'#[0-9a-f]{6}', value or ''):
        return None
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _towiki_nearest_palette_index(rgb, palette):
    if not rgb:
        return None
    best = None
    best_dist = None
    for enum_value, color in palette.items():
        dist = sum((rgb[i] - color[i]) ** 2 for i in range(3))
        if best_dist is None or dist < best_dist:
            best = enum_value
            best_dist = dist
    return best


def _towiki_css_color_to_feishu_enum(value, field):
    rgb = _towiki_hex_to_rgb(value)
    if not rgb:
        return None
    if field == 'text_color':
        if max(rgb) - min(rgb) <= 18 and sum(rgb) / 3 <= 130:
            return None
        # Feishu docx text colors are integer enums. Neutral body text colors
        # stay unset so Feishu renders them as the default black text.
        palette = {
            1: (216, 57, 49),
            2: (222, 120, 2),
            3: (220, 155, 4),
            4: (46, 161, 33),
            5: (36, 91, 219),
            6: (100, 37, 208),
            7: (80, 80, 80),
        }
        if rgb in ((0, 0, 0), (255, 255, 255)):
            return None
        return _towiki_nearest_palette_index(rgb, palette)
    if field == 'background_color':
        palette = {
            1: (251, 191, 188),
            2: (254, 212, 164),
            3: (249, 237, 166),
            4: (183, 237, 177),
            5: (186, 206, 253),
            6: (205, 178, 250),
            7: (222, 224, 227),
            8: (216, 57, 49),
            9: (222, 120, 2),
            10: (220, 155, 4),
            11: (46, 161, 33),
            12: (36, 91, 219),
            13: (100, 37, 208),
            14: (80, 80, 80),
            15: (245, 246, 247),
        }
        if rgb == (255, 255, 255):
            return None
        return _towiki_nearest_palette_index(rgb, palette)
    return None


def _towiki_sanitize_text_style(style, keep_colors=True):
    allowed = {
        'bold', 'italic', 'strikethrough', 'underline', 'inline_code',
        'text_color', 'background_color', 'link',
    }
    cleaned = {}
    for key, value in (style or {}).items():
        if key not in allowed or value in ('', None, False):
            continue
        if key in ('text_color', 'background_color'):
            if not keep_colors:
                continue
            max_enum = 7 if key == 'text_color' else 15
            if isinstance(value, int) and not isinstance(value, bool):
                if not 1 <= value <= max_enum:
                    continue
            elif isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
                if not 1 <= value <= max_enum:
                    continue
            else:
                value = _towiki_normalize_css_color(str(value))
                if not value:
                    continue
                enum_value = _towiki_css_color_to_feishu_enum(value, key)
                if enum_value is None:
                    continue
                value = enum_value
        if key == 'link':
            url = value.get('url') if isinstance(value, dict) else str(value)
            url = _towiki_normalize_url(url)
            if not url:
                continue
            value = {'url': url}
        cleaned[key] = value
    return cleaned


def _towiki_block_without_colors(block):
    copied = json.loads(json.dumps(block, ensure_ascii=False))
    for value in copied.values():
        if not isinstance(value, dict):
            continue
        for el in value.get('elements', []):
            style = el.get('text_run', {}).get('text_element_style')
            if style:
                style.pop('text_color', None)
                style.pop('background_color', None)
                if not style:
                    el.get('text_run', {}).pop('text_element_style', None)
    return copied


def _towiki_plain_text_block(block):
    if not isinstance(block, dict):
        return block
    block_type = block.get('block_type', 2)
    if block_type not in (2, 3, 4, 5, 6, 12, 13):
        block_type = 2
    text = ''
    for value in block.values():
        if isinstance(value, dict):
            text = ''.join(
                el.get('text_run', {}).get('content', '')
                for el in value.get('elements', [])
            )
            if text:
                break
    return _towiki_text_block(text or ' ', block_type)


def _towiki_parse_inline_style(tag, base_url=''):
    style = {}
    if not tag:
        return style
    name = getattr(tag, 'name', '') or ''
    classes = set(tag.get('class', []) or [])
    if name in ('strong', 'b') or 'bold' in classes:
        style['bold'] = True
    if name in ('em', 'i') or 'italic' in classes:
        style['italic'] = True
    if name == 'u' or 'underline' in classes:
        style['underline'] = True
    if name in ('s', 'strike', 'del') or 'strike' in classes:
        style['strikethrough'] = True
    if name == 'code' or 'code' in classes:
        style['inline_code'] = True
    css = tag.get('style', '') or ''
    for declaration in css.split(';'):
        if ':' not in declaration:
            continue
        prop, value = declaration.split(':', 1)
        prop = prop.strip().lower()
        value = value.strip()
        if prop == 'color':
            color = _towiki_normalize_css_color(value)
            if color:
                style['text_color'] = color
        elif prop == 'background-color':
            color = _towiki_normalize_css_color(value)
            if color:
                style['background_color'] = color
    href = tag.get('href', '')
    if href:
        url = _towiki_normalize_url(href, base_url)
        if url:
            style['link'] = {'url': url}
    return style


def _towiki_collect_inline_elements(node, inherited=None, base_url=''):
    inherited = dict(inherited or {})
    if getattr(node, 'name', None):
        inherited.update(_towiki_parse_inline_style(node, base_url))
    elements = []
    for child in getattr(node, 'children', []):
        if getattr(child, 'name', None) is None:
            text = str(child)
            if text:
                elements.append(_towiki_make_text_element(text, inherited or None))
            continue
        child_style = dict(inherited)
        child_style.update(_towiki_parse_inline_style(child, base_url))
        if child.name == 'br':
            elements.append(_towiki_make_text_element('\n', inherited or None))
            continue
        if child.name == 'img':
            alt = child.get('alt', '') or ''
            if alt:
                elements.append(_towiki_make_text_element(alt, inherited or None))
            continue
        if child.name == 'a':
            href = child.get('href', '')
            child_style = dict(child_style)
            if href:
                url = _towiki_normalize_url(href, base_url)
                if url:
                    child_style['link'] = {'url': url}
        nested = _towiki_collect_inline_elements(child, child_style, base_url)
        if nested:
            elements.extend(nested)
    return elements


def _towiki_dom_to_blocks(tag, block_type, base_url=''):
    elements = _towiki_collect_inline_elements(tag, base_url=base_url)
    text = ''.join(el.get('text_run', {}).get('content', '') for el in elements).strip()
    if not text:
        return []
    return _towiki_split_rich_elements(elements, block_type)


def _towiki_dom_to_block(tag, block_type, base_url=''):
    blocks = _towiki_dom_to_blocks(tag, block_type, base_url)
    return blocks[0] if blocks else None


def _towiki_split_text_blocks(text, block_type=2, limit=1800):
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return []
    chunks = []
    while len(text) > limit:
        cut = max(text.rfind('\n', 0, limit), text.rfind('。', 0, limit),
                  text.rfind('.', 0, limit))
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return [_towiki_text_block(chunk, block_type) for chunk in chunks if chunk]


def _towiki_pdf_line_block_type(line):
    text = re.sub(r'\s+', ' ', line).strip()
    if not text:
        return None

    # PDF 会把正文断成很多短行，标题识别必须保守，避免短正文被误判。
    if re.match(r'^第[一二三四五六七八九十百千\d]+[章节篇部分][、.\s]', text):
        return 4
    if re.match(r'^[一二三四五六七八九十]+[、.．]', text) and len(text) <= 28:
        return 4
    if re.match(r'^（[一二三四五六七八九十]+）', text) and len(text) <= 28:
        return 5
    if re.match(r'^[0-9]+(\.[0-9]+){1,2}[、.\s]', text):
        return 5 if re.match(r'^[0-9]+\.[0-9]+\.[0-9]+[、.\s]', text) else 4
    ascii_letters = re.sub(r'[^A-Za-z]', '', text)
    if (len(text) <= 24 and ascii_letters and text.upper() == text and
            len(ascii_letters) / max(len(text), 1) >= 0.6):
        return 4
    return 2


def _towiki_pdf_line_block_type_from_line(line):
    text = (line.get('text') or '').strip()
    size = float(line.get('size') or 0)
    if size >= 16 and len(text) <= 80:
        return 3
    return _towiki_pdf_line_block_type(text)


def _towiki_pdf_ordered_marker(text):
    return re.match(r'^[0-9]+[、.．]\s*(.+)$', text or '')


def _towiki_pdf_bullet_marker(text):
    return (text or '').startswith(('\uf101', '•', '·', '●', '○', '▪', '‣', '–', '-', '—'))


def _towiki_pdf_text_to_blocks(text):
    blocks = []
    for raw_line in re.split(r'\n+', text or ''):
        line = raw_line.strip()
        if not line:
            continue
        block_type = _towiki_pdf_line_block_type(line)
        if block_type is None:
            continue
        blocks.extend(_towiki_split_text_blocks(line, block_type))
    return blocks


def _towiki_pdf_rect_list(rect):
    if rect is None:
        return [0, 0, 0, 0]
    if hasattr(rect, 'x0'):
        return [rect.x0, rect.y0, rect.x1, rect.y1]
    return list(rect)


def _towiki_pdf_rect_area(rect):
    x0, y0, x1, y1 = _towiki_pdf_rect_list(rect)
    return max(0, x1 - x0) * max(0, y1 - y0)


def _towiki_pdf_rect_intersects(a, b):
    ax0, ay0, ax1, ay1 = _towiki_pdf_rect_list(a)
    bx0, by0, bx1, by1 = _towiki_pdf_rect_list(b)
    return not (ax1 < bx0 or ax0 > bx1 or ay1 < by0 or ay0 > by1)


def _towiki_pdf_rect_overlap_ratio(a, b):
    ax0, ay0, ax1, ay1 = _towiki_pdf_rect_list(a)
    bx0, by0, bx1, by1 = _towiki_pdf_rect_list(b)
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    area = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    base = _towiki_pdf_rect_area(a)
    return area / base if base else 0


def _towiki_pdf_union_bbox(items):
    boxes = []
    for item in items or []:
        bbox = item.get('bbox') if isinstance(item, dict) else None
        if bbox:
            boxes.append(_towiki_pdf_rect_list(bbox))
    if not boxes:
        return [0, 0, 0, 0]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _towiki_pdf_rect_center_inside(inner, outer):
    x0, y0, x1, y1 = _towiki_pdf_rect_list(inner)
    ox0, oy0, ox1, oy1 = _towiki_pdf_rect_list(outer)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return ox0 <= cx <= ox1 and oy0 <= cy <= oy1


def _towiki_pdf_color_int_to_hex(value):
    if value in ('', None):
        return ''
    try:
        value = int(value)
    except (TypeError, ValueError):
        return ''
    if value < 0:
        return ''
    return '#%02x%02x%02x' % (
        (value >> 16) & 255,
        (value >> 8) & 255,
        value & 255,
    )


def _towiki_pdf_color_tuple_to_hex(value):
    if not value or len(value) < 3:
        return ''
    try:
        rgb = [float(v) for v in value[:3]]
    except (TypeError, ValueError):
        return ''
    if all(0 <= v <= 1 for v in rgb):
        rgb = [round(v * 255) for v in rgb]
    else:
        rgb = [round(v) for v in rgb]
    if not all(0 <= v <= 255 for v in rgb):
        return ''
    return '#%02x%02x%02x' % tuple(int(v) for v in rgb)


def _towiki_pdf_background_rects(page):
    backgrounds = []
    page_area = _towiki_pdf_rect_area(page.rect)
    try:
        drawings = page.get_drawings()
    except Exception:
        return backgrounds
    for drawing in drawings:
        fill = drawing.get('fill')
        rect = drawing.get('rect')
        color = _towiki_pdf_color_tuple_to_hex(fill)
        if not rect or not color:
            continue
        bbox = _towiki_pdf_rect_list(rect)
        area = _towiki_pdf_rect_area(bbox)
        if area <= 1 or (page_area and area > page_area * 0.85):
            continue
        if _towiki_css_color_to_feishu_enum(color, 'background_color') is None:
            continue
        backgrounds.append({'bbox': bbox, 'color': color, 'area': area})
    return backgrounds


def _towiki_pdf_background_for_bbox(backgrounds, bbox):
    matches = []
    x0, y0, x1, y1 = _towiki_pdf_rect_list(bbox)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for bg in backgrounds:
        bx0, by0, bx1, by1 = bg['bbox']
        center_inside = bx0 - 3 <= cx <= bx1 + 3 and by0 <= cy <= by1
        overlap = _towiki_pdf_rect_overlap_ratio(bbox, bg['bbox'])
        if center_inside or overlap >= 0.55:
            matches.append((bg['area'], bg['color']))
    if not matches:
        return ''
    matches.sort()
    return matches[0][1]


def _towiki_pdf_link_for_bbox(links, bbox):
    for lk in links:
        rect = lk['rect']
        if (_towiki_pdf_rect_center_inside(bbox, rect) or
                _towiki_pdf_rect_overlap_ratio(bbox, rect) >= 0.35):
            return lk['uri']
    return ''


def _towiki_pdf_span_base_style(span):
    style = {}
    font = span.get('font') or ''
    font_l = font.lower()
    flags = int(span.get('flags') or 0)
    if (flags & 16 or font_l.startswith('type3') or
            re.search(r'(bold|semibold|demi|heavy|black)', font_l)):
        style['bold'] = True
    if flags & 2 or re.search(r'(italic|oblique)', font_l):
        style['italic'] = True
    color = _towiki_pdf_color_int_to_hex(span.get('color'))
    color_enum = _towiki_css_color_to_feishu_enum(color, 'text_color') if color else None
    if color_enum is not None:
        style['text_color'] = color
        if color_enum in (1, 5, 6):
            style['bold'] = True
    return style


def _towiki_pdf_char_width_weight(ch):
    if not ch:
        return 0
    if ch.isspace():
        return 0.35
    if ord(ch) < 128:
        return 0.58
    return 1.0


def _towiki_pdf_expand_span(span, links, backgrounds):
    text = span.get('text', '')
    bbox = span.get('bbox') or [0, 0, 0, 0]
    if not text:
        return []

    x0, y0, x1, y1 = _towiki_pdf_rect_list(bbox)
    total_width = max(0, x1 - x0)
    weights = [_towiki_pdf_char_width_weight(ch) for ch in text]
    weight_sum = sum(weights) or len(text) or 1
    base_style = _towiki_pdf_span_base_style(span)
    font = span.get('font') or ''
    size = float(span.get('size') or 0)

    pieces = []
    cur_x = x0
    for ch, weight in zip(text, weights):
        next_x = cur_x + total_width * weight / weight_sum
        char_bbox = [cur_x, y0, next_x, y1]
        style = dict(base_style)
        background = _towiki_pdf_background_for_bbox(backgrounds, char_bbox)
        if background:
            style['background_color'] = background
            style['bold'] = True
        link = _towiki_pdf_link_for_bbox(links, char_bbox)
        if link:
            style['link'] = {'url': link}
        if pieces and pieces[-1].get('style') == style:
            pieces[-1]['text'] += ch
            pieces[-1]['bbox'][2] = next_x
        else:
            pieces.append({
                'text': ch,
                'font': font,
                'size': size,
                'style': style,
                'bbox': list(char_bbox),
            })
        cur_x = next_x
    return pieces


def _towiki_pdf_trim_spans_to_text(spans, target_text):
    full_text = ''.join(span.get('text', '') for span in spans)
    start = full_text.find(target_text)
    if start <= 0:
        return spans
    remaining = start
    trimmed = []
    for span in spans:
        text = span.get('text', '')
        if remaining >= len(text):
            remaining -= len(text)
            continue
        item = dict(span)
        item['text'] = text[remaining:]
        trimmed.append(item)
        remaining = 0
    return trimmed


def _towiki_pdf_make_elements_from_spans(spans):
    elements = []
    merged = []
    for span in spans:
        text = span.get('text', '')
        if not text:
            continue
        style = span.get('style') or {}
        if merged and merged[-1].get('style') == style:
            merged[-1]['text'] += text
        else:
            merged.append({'text': text, 'style': style})
    for span in merged:
        elements.append(_towiki_make_text_element(span['text'], span.get('style')))
    return elements


def _towiki_pdf_line_to_block(line):
    text = line.get('text', '').strip()
    if not text:
        return None
    block_type = _towiki_pdf_line_block_type_from_line(line) or 2
    cleaned = text
    if _towiki_pdf_bullet_marker(cleaned):
        cleaned = cleaned.lstrip('\uf101•·●○▪‣–-— ').strip()
        if block_type == 2:
            block_type = 12
    text = cleaned or text
    spans = line.get('spans') or []
    if spans:
        spans = _towiki_pdf_trim_spans_to_text(spans, text)
        elements = _towiki_pdf_make_elements_from_spans(spans)
    else:
        style = {}
        if line.get('link'):
            style['link'] = {'url': line['link']}
        elements = [_towiki_make_text_element(text, style)]
    key_map = {
        2: 'text', 3: 'heading1', 4: 'heading2', 5: 'heading3',
        6: 'heading4', 12: 'bullet', 13: 'ordered',
    }
    key = key_map.get(block_type, 'text')
    return {'block_type': block_type, key: {'elements': elements}}


def _towiki_pdf_lines_to_blocks(lines):
    blocks = []
    paragraph_lines = []

    def joiner(prev_text, next_text):
        prev_text = (prev_text or '').rstrip()
        next_text = (next_text or '').lstrip()
        if not prev_text or not next_text:
            return ''
        if prev_text.endswith('-'):
            return ''
        if re.search(r'[A-Za-z0-9]$', prev_text) and re.match(r'[A-Za-z0-9]', next_text):
            return ' '
        return ''

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        elements = []
        prev_text = ''
        for line in paragraph_lines:
            if elements:
                sep = joiner(prev_text, line.get('text', ''))
                if sep:
                    elements.append(_towiki_make_text_element(sep))
            elements.extend(_towiki_pdf_make_elements_from_spans(line.get('spans') or []))
            prev_text = line.get('text', '')
        blocks.extend(_towiki_split_rich_elements(elements, 2))
        paragraph_lines = []

    for line in lines:
        text = line.get('text', '').strip()
        block_type = _towiki_pdf_line_block_type_from_line(line) or 2
        is_list = _towiki_pdf_bullet_marker(text)
        if block_type == 2 and not is_list:
            paragraph_lines.append(line)
            continue
        flush_paragraph()
        block = _towiki_pdf_line_to_block(line)
        if block:
            blocks.append(block)
    flush_paragraph()
    return blocks


def _towiki_docling_ref_id(ref):
    if not isinstance(ref, dict):
        return None, None
    value = ref.get('$ref') or ''
    m = re.match(r'^#/(texts|pictures|groups)/(\d+)$', value)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def _towiki_docling_bbox_to_pymupdf(prov, pages):
    if not prov:
        return None
    bbox = prov.get('bbox') or {}
    page_no = int(prov.get('page_no') or 0)
    if not page_no:
        return None
    l, t = float(bbox.get('l', 0)), float(bbox.get('t', 0))
    r, b = float(bbox.get('r', 0)), float(bbox.get('b', 0))
    if bbox.get('coord_origin') == 'BOTTOMLEFT':
        page = pages.get(str(page_no), {}) if isinstance(pages, dict) else {}
        height = float(page.get('size', {}).get('height') or 0)
        if height:
            return page_no, [l, height - t, r, height - b]
    return page_no, [l, t, r, b]


def _towiki_docling_flatten_refs(data, refs, parent_label=''):
    items = []
    for ref in refs:
        kind, index = _towiki_docling_ref_id(ref)
        if kind is None:
            continue
        if kind == 'groups':
            group = data.get('groups', [])[index]
            label = group.get('label') or group.get('name') or parent_label
            items.extend(_towiki_docling_flatten_refs(
                data, group.get('children', []), label
            ))
            continue
        item = data.get(kind, [])[index]
        copied = dict(item)
        copied['_towiki_docling_kind'] = kind[:-1]
        copied['_towiki_parent_label'] = parent_label
        items.append(copied)
    return items


def _towiki_docling_proxy_issue():
    for env_name in ('NO_PROXY', 'no_proxy'):
        value = os.environ.get(env_name, '')
        for entry in (part.strip() for part in value.split(',')):
            host = entry.split('/', 1)[0]
            if host.count(':') >= 2 and not host.startswith('['):
                return (
                    f'{env_name} 含裸 IPv6 条目 {entry!r}，'
                    '当前 httpx 会将其误解析为端口'
                )
    return ''


def _towiki_docling_structure(pdf_path):
    proxy_issue = _towiki_docling_proxy_issue()
    if proxy_issue:
        print(f'  Docling 已跳过：{proxy_issue}；使用 PyMuPDF',
              flush=True)
        return None

    try:
        sys.modules['mlx'] = None
        sys.modules['mlx_whisper'] = None
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
    except Exception as e:
        print(f'  Docling 不可用，回退 PyMuPDF: {e}', flush=True)
        return None

    try:
        options = PdfPipelineOptions(do_ocr=False)
        converter = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options)
        })
        result = converter.convert(str(pdf_path))
        data = result.document.export_to_dict()
    except Exception as e:
        print(f'  Docling 结构解析失败，回退 PyMuPDF: {e}', flush=True)
        return None

    refs = data.get('body', {}).get('children', [])
    items = _towiki_docling_flatten_refs(data, refs)
    if not items:
        return None
    return {'items': items, 'pages': data.get('pages', {})}


def _towiki_pdf_line_center(line):
    x0, y0, x1, y1 = _towiki_pdf_rect_list(line.get('bbox'))
    return (x0 + x1) / 2, (y0 + y1) / 2


def _towiki_pdf_lines_in_bbox(lines, bbox):
    if not bbox:
        return []
    x0, y0, x1, y1 = _towiki_pdf_rect_list(bbox)
    matched = []
    for line in lines:
        cx, cy = _towiki_pdf_line_center(line)
        if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
            matched.append(line)
            continue
        if _towiki_pdf_rect_overlap_ratio(line.get('bbox'), bbox) >= 0.35:
            matched.append(line)
    return matched


def _towiki_pdf_images_in_bbox(images, bbox, used):
    matches = []
    for image in images:
        if id(image) in used:
            continue
        overlap = _towiki_pdf_rect_overlap_ratio(image.get('bbox'), bbox)
        if overlap >= 0.2:
            matches.append(image)
    matches.sort(key=lambda image: (
        round((image.get('bbox') or [0, 0, 0, 0])[1], 1),
        round((image.get('bbox') or [0, 0, 0, 0])[0], 1),
    ))
    for image in matches:
        used.add(id(image))
    return matches


def _towiki_pdf_clean_docling_list_lines(lines):
    if not lines:
        return []
    copied = [dict(line) for line in lines]
    for line in copied:
        line['spans'] = [dict(span) for span in line.get('spans', [])]

    for line in copied:
        line_text = (line.get('text') or '').strip()
        if not line_text:
            continue
        cleaned = line_text
        if _towiki_pdf_bullet_marker(cleaned):
            cleaned = cleaned.lstrip('\uf101•·●○▪‣–-— ').strip()
        m = _towiki_pdf_ordered_marker(cleaned)
        if m:
            cleaned = m.group(1).strip()
        if cleaned != line_text:
            if cleaned:
                line['spans'] = _towiki_pdf_trim_spans_to_text(
                    line.get('spans') or [], cleaned
                )
                line['text'] = ''.join(span.get('text', '') for span in line['spans'])
            else:
                line['spans'] = []
                line['text'] = ''
        break
    return [line for line in copied if line.get('text') or line.get('spans')]


def _towiki_pdf_lines_to_one_block(lines, block_type=2):
    elements = []
    prev_text = ''
    for line in lines:
        if elements:
            sep = ''
            if re.search(r'[A-Za-z0-9]$', prev_text or '') and re.match(
                r'[A-Za-z0-9]', line.get('text', '')
            ):
                sep = ' '
            if sep:
                elements.append(_towiki_make_text_element(sep))
        elements.extend(_towiki_pdf_make_elements_from_spans(line.get('spans') or []))
        prev_text = line.get('text', '')
    if not elements:
        return []
    return _towiki_split_rich_elements(elements, block_type)


def _towiki_pdf_block_key(block):
    if not isinstance(block, dict) or block.get('_towiki_image'):
        return None
    for key in (
        'text', 'heading1', 'heading2', 'heading3', 'heading4', 'heading5',
        'heading6', 'bullet', 'ordered'
    ):
        if key in block:
            return key
    return None


def _towiki_pdf_block_text(block):
    key = _towiki_pdf_block_key(block)
    if not key:
        return ''
    return ''.join(
        el.get('text_run', {}).get('content', '')
        for el in block.get(key, {}).get('elements', [])
    )


def _towiki_pdf_block_with_meta(block, page_no, bbox):
    if not isinstance(block, dict):
        return block
    copied = dict(block)
    copied['_towiki_page_no'] = page_no
    copied['_towiki_bbox'] = list(bbox or [0, 0, 0, 0])
    return copied


def _towiki_pdf_strip_meta(block):
    if not isinstance(block, dict):
        return block
    copied = dict(block)
    copied.pop('_towiki_page_no', None)
    copied.pop('_towiki_bbox', None)
    return copied


def _towiki_pdf_vertical_gap(prev, current):
    if prev.get('_towiki_page_no') != current.get('_towiki_page_no'):
        return None
    prev_bbox = prev.get('_towiki_bbox')
    current_bbox = current.get('_towiki_bbox')
    if not prev_bbox or not current_bbox:
        return None
    return _towiki_pdf_rect_list(current_bbox)[1] - _towiki_pdf_rect_list(prev_bbox)[3]


def _towiki_pdf_joiner(prev_text, next_text):
    if (prev_text or '').endswith('-'):
        return ''
    if re.search(r'[A-Za-z0-9]$', prev_text or '') and re.match(
        r'[A-Za-z0-9]', next_text or ''
    ):
        return ' '
    return ''


def _towiki_pdf_should_merge_wrapped_block(prev, current):
    if prev.get('_towiki_image') or current.get('_towiki_image'):
        return False
    if prev.get('block_type') not in (2, 12) or current.get('block_type') != 2:
        return False
    gap = _towiki_pdf_vertical_gap(prev, current)
    if gap is not None and (gap < -2 or gap > 24):
        return False
    prev_text = _towiki_pdf_block_text(prev).rstrip()
    current_text = _towiki_pdf_block_text(current).lstrip()
    if not prev_text or not current_text:
        return False
    if prev_text.startswith('---') or current_text.startswith('---'):
        return False
    if current_text.startswith(('常驻小尾巴', '作者：')):
        return False
    if prev_text.endswith(tuple('。！？!?；;：:）》」】])）)')):
        return False
    if re.match(r'^(黄钊hanniman评注|[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十]+）|[0-9]+[、.．]|[a-zA-Z][）)])', current_text):
        return False
    return True


def _towiki_pdf_merge_wrapped_blocks(blocks):
    merged = []
    for block in blocks:
        if merged and _towiki_pdf_should_merge_wrapped_block(merged[-1], block):
            prev_key = _towiki_pdf_block_key(merged[-1])
            current_key = _towiki_pdf_block_key(block)
            if not prev_key or not current_key:
                merged.append(block)
                continue
            prev_elements = merged[-1][prev_key].setdefault('elements', [])
            current_elements = block[current_key].get('elements', [])
            sep = _towiki_pdf_joiner(
                _towiki_pdf_block_text(merged[-1]),
                _towiki_pdf_block_text(block),
            )
            if sep:
                prev_elements.append(_towiki_make_text_element(sep))
            prev_elements.extend(current_elements)
            continue
        merged.append(block)
    return merged


def _towiki_pdf_docling_text_blocks(item, native_lines):
    lines = native_lines
    if not lines:
        return []
    label = item.get('label') or item.get('_towiki_parent_label') or ''
    first = lines[0]
    block_type = _towiki_pdf_line_block_type_from_line(first) or 2
    full_text = ''.join(line.get('text', '') for line in lines).strip()

    if label == 'list_item' or item.get('_towiki_parent_label') == 'list':
        has_bullet_marker = any(
            _towiki_pdf_bullet_marker(line.get('text', '').strip())
            for line in lines
        )
        if not has_bullet_marker:
            return _towiki_pdf_lines_to_one_block(lines, 2)
        cleaned_lines = _towiki_pdf_clean_docling_list_lines(lines)
        if not cleaned_lines:
            return []
        text = ''.join(line.get('text', '') for line in cleaned_lines).strip()
        if has_bullet_marker and text:
            block_type = 12
        return _towiki_pdf_lines_to_one_block(cleaned_lines, block_type)

    if block_type in (3, 4, 5, 6):
        return _towiki_pdf_lines_to_one_block(lines, block_type)
    return _towiki_pdf_lines_to_one_block(lines, 2)


def _towiki_pdf_blocks_with_docling(pdf_path, native_data):
    structure = _towiki_docling_structure(pdf_path)
    if not structure:
        return None

    pages = native_data.get('pages', [])
    blocks = []
    used_images = set()
    used_lines = set()
    events = []
    for order, item in enumerate(structure['items']):
        prov = (item.get('prov') or [None])[0]
        if item.get('_towiki_docling_kind') == 'picture':
            continue
        converted = _towiki_docling_bbox_to_pymupdf(prov, structure.get('pages', {}))
        if not converted:
            continue
        page_no, bbox = converted
        if page_no < 1 or page_no > len(pages):
            continue
        events.append({
            'kind': 'text',
            'page_no': page_no,
            'bbox': bbox,
            'item': item,
            'order': order,
        })

    for page_no, page in enumerate(pages, 1):
        for order, image in enumerate(
            [i for i in page.get('items', []) if i.get('kind') == 'image']
        ):
            events.append({
                'kind': 'image',
                'page_no': page_no,
                'bbox': image.get('bbox') or [0, 0, 0, 0],
                'image': image,
                'order': order,
            })

    events.sort(key=lambda event: (
        event['page_no'],
        round((event.get('bbox') or [0, 0, 0, 0])[1], 1),
        round((event.get('bbox') or [0, 0, 0, 0])[0], 1),
        1 if event.get('kind') == 'image' else 0,
        event.get('order', 0),
    ))

    for event in events:
        page_no = event['page_no']
        page = pages[page_no - 1]
        if event['kind'] == 'image':
            image = event['image']
            if id(image) in used_images:
                continue
            used_images.add(id(image))
            image_block = _towiki_image_marker(
                image['path'],
                filename=image.get('name', ''),
                content_type=image.get('content_type', 'image/png'),
            )
            blocks.append(_towiki_pdf_block_with_meta(
                image_block, page_no, image.get('bbox') or [0, 0, 0, 0]
            ))
            continue

        native_lines = [
            line for line in _towiki_pdf_lines_in_bbox(page.get('lines', []), event['bbox'])
            if id(line) not in used_lines
        ]
        for line in native_lines:
            used_lines.add(id(line))
        new_blocks = _towiki_pdf_docling_text_blocks(event['item'], native_lines)
        native_bbox = _towiki_pdf_union_bbox(native_lines) or event['bbox']
        blocks.extend(
            _towiki_pdf_block_with_meta(block, page_no, native_bbox)
            for block in new_blocks
        )

    merged = _towiki_pdf_merge_wrapped_blocks(blocks)
    return [_towiki_pdf_strip_meta(block) for block in merged] or None


def _towiki_image_marker(source, base_url='', filename='', content_type=''):
    return {
        '_towiki_image': True,
        'source': source,
        'base_url': base_url,
        'filename': filename,
        'content_type': content_type,
    }


def _towiki_normalize_mime_type(content_type, filename=''):
    if content_type and '/' in content_type:
        return content_type
    guessed = mimetypes.guess_type(filename or '')[0]
    if guessed:
        return guessed
    if content_type:
        return f'image/{content_type.strip().lower()}'
    return 'image/png'


def _towiki_is_transient_error(error):
    if isinstance(error, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.ChunkedEncodingError,
    )):
        return True
    if isinstance(error, requests.exceptions.HTTPError):
        status = getattr(error.response, 'status_code', 0)
        return status == 429 or status >= 500
    message = str(error).lower()
    return (
        '429' in message
        or '限流' in message
        or re.search(r'\b(?:http|status)[ =:]*(5\d\d)\b', message) is not None
    )


def _towiki_is_access_error(error):
    message = str(error).lower()
    return (
        '认证失败' in message
        or '权限失败' in message
        or re.search(r'\bhttp[ =:]*(401|403)\b', message) is not None
    )


def _towiki_get_with_retry(client, url, headers, timeout, label, retries=3):
    for attempt in range(1, retries + 1):
        try:
            resp = client._session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            if attempt > 1:
                print(f'  {label}重试成功（尝试 {attempt}/{retries}）',
                      flush=True)
            return resp
        except Exception as error:
            if not _towiki_is_transient_error(error) or attempt == retries:
                raise
            wait_seconds = 2 * attempt
            print(f'  {label}失败（尝试 {attempt}/{retries}）：{error}；'
                  f'{wait_seconds} 秒后重试', flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f'{label}重试流程异常结束')


def _towiki_download_image(client, image_item, retries=3):
    source = image_item['source']
    if isinstance(source, (bytes, bytearray)):
        filename = image_item.get('filename') or 'image.png'
        content_type = _towiki_normalize_mime_type(
            image_item.get('content_type'), filename)
        return bytes(source), content_type, filename

    local_path = Path(str(source)).expanduser()
    if local_path.exists():
        content_type = _towiki_normalize_mime_type(
            image_item.get('content_type'), str(local_path))
        return local_path.read_bytes(), content_type, local_path.name

    img_url = urljoin(image_item.get('base_url', ''), source)
    headers = {'User-Agent': 'Mozilla/5.0'}
    if 'zsxq.com' in img_url:
        token = client.credentials.get('zsxq', {}).get('access_token', '')
        if token:
            headers['Cookie'] = f'zsxq_access_token={token}'
    resp = _towiki_get_with_retry(
        client, img_url, headers, 30, '图片下载', retries)
    content_type = resp.headers.get('Content-Type', '').split(';')[0] or 'image/png'
    ext = mimetypes.guess_extension(content_type) or Path(urlparse(img_url).path).suffix
    filename = Path(urlparse(img_url).path).name or f'image{ext or ".png"}'
    return resp.content, content_type, filename


def _towiki_upload_image_to_block(client, doc_id, block_id, image_bytes,
                                  filename, content_type):
    upload_url = f'{client.base_url}/drive/v1/medias/upload_all'
    extra = json.dumps({'drive_route_token': doc_id})
    resp = client._session.post(
        upload_url,
        headers=_towiki_auth_headers(client),
        files={'file': (filename, image_bytes, content_type or 'image/png')},
        data={
            'file_name': filename,
            'parent_type': 'docx_image',
            'parent_node': block_id,
            'size': str(len(image_bytes)),
            'extra': extra,
        },
        timeout=60,
    )
    data = _towiki_api_json(resp, '上传图片')
    file_token = data.get('file_token')
    if not file_token:
        raise RuntimeError('上传图片未返回 file_token')

    patch_url = f'{client.base_url}/docx/v1/documents/{doc_id}/blocks/{block_id}'
    resp = client._session.patch(
        patch_url,
        json={'replace_image': {'token': file_token}, 'block_id': block_id},
        headers=_towiki_json_headers(client),
        params={'document_revision_id': '-1'},
        timeout=30,
    )
    _towiki_api_json(resp, '绑定图片')


def _towiki_write_image(client, doc_id, parent_id, image_item):
    image_bytes, content_type, filename = _towiki_download_image(client, image_item)
    url = f'{client.base_url}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children'
    resp = client._session.post(
        url,
        json={'children': [{'block_type': 27, 'image': {}}], 'index': -1},
        headers=_towiki_json_headers(client),
        params={'document_revision_id': '-1'},
        timeout=30,
    )
    data = _towiki_api_json(resp, '创建图片 block')
    block_id = data['children'][0]['block_id']
    _towiki_upload_image_to_block(
        client, doc_id, block_id, image_bytes, filename, content_type
    )


def _towiki_append_blocks(client, doc_id, blocks, progress_callback=None):
    parent_id = _towiki_get_document_root(client, doc_id)
    normal_buf = []
    colors_disabled = False
    written_count = 0

    def is_rate_limited(err):
        return '429' in str(err) or '限流' in str(err)

    def write_batch(url, batch, label, retries=5):
        for attempt in range(retries):
            resp = client._session.post(
                url,
                json={'children': batch, 'index': -1},
                headers=_towiki_json_headers(client),
                params={'document_revision_id': '-1'},
                timeout=30,
            )
            try:
                return _towiki_api_json(resp, label)
            except RuntimeError as e:
                if not is_rate_limited(e) or attempt == retries - 1:
                    raise
                wait = 3 * (attempt + 1)
                print(f'  {label} 遇到限流，等待 {wait}s 后重试...', flush=True)
                time.sleep(wait)
        return None

    def write_single_or_downgrade(url, block, index):
        nonlocal colors_disabled
        try:
            target_block = _towiki_block_without_colors(block) if colors_disabled else block
            write_batch(url, [target_block], f'写入文本 block[{index}]')
            return
        except RuntimeError as e:
            if is_rate_limited(e) or _towiki_is_access_error(e) or _towiki_is_transient_error(e):
                raise
            downgraded = _towiki_block_without_colors(block)
            if downgraded == block:
                print(f'  block[{index}] 富文本样式不兼容，改用纯文本: {e}', flush=True)
                write_batch(url, [_towiki_plain_text_block(block)],
                            f'写入纯文本 block[{index}]')
                return
            print(f'  block[{index}] 颜色样式不兼容，去掉颜色后重试: {e}', flush=True)
            try:
                write_batch(url, [downgraded], f'写入降级文本 block[{index}]')
                colors_disabled = True
            except RuntimeError as e2:
                if (is_rate_limited(e2) or _towiki_is_access_error(e2)
                        or _towiki_is_transient_error(e2)):
                    raise
                print(f'  block[{index}] 降级后仍不兼容，改用纯文本: {e2}', flush=True)
                write_batch(url, [_towiki_plain_text_block(block)],
                            f'写入纯文本 block[{index}]')

    def flush():
        nonlocal normal_buf, colors_disabled, written_count
        if not normal_buf:
            return
        url = f'{client.base_url}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children'
        for i in range(0, len(normal_buf), 20):
            batch = normal_buf[i:i + 20]
            if colors_disabled:
                batch = [_towiki_block_without_colors(block) for block in batch]
            try:
                write_batch(url, batch, '写入文本 blocks')
            except RuntimeError as e:
                if _towiki_is_access_error(e) or _towiki_is_transient_error(e):
                    raise
                print(f'  批量写入失败，拆成单块重试: {e}', flush=True)
                if not colors_disabled and any(
                    _towiki_block_without_colors(block) != block for block in batch
                ):
                    colors_disabled = True
                    no_color_batch = [_towiki_block_without_colors(block) for block in batch]
                    try:
                        write_batch(url, no_color_batch, '写入去色文本 blocks')
                        time.sleep(0.5)
                        continue
                    except RuntimeError as e2:
                        if (_towiki_is_access_error(e2)
                                or _towiki_is_transient_error(e2)):
                            raise
                        print(f'  去色批量写入仍失败，继续单块重试: {e2}', flush=True)
                for j, block in enumerate(batch):
                    write_single_or_downgrade(url, block, i + j)
            written_count += len(batch)
            if progress_callback:
                progress_callback(written_count)
            time.sleep(0.5)
        normal_buf = []

    for block in blocks:
        if isinstance(block, dict) and block.get('_towiki_image'):
            flush()
            _towiki_write_image(client, doc_id, parent_id, block)
            written_count += 1
            if progress_callback:
                progress_callback(written_count)
            time.sleep(0.3)
        else:
            normal_buf.append(block)
    flush()
    return written_count


def _towiki_select_content(soup):
    selectors = [
        'article', '.article', '.article-content', '.content',
        '.topic-detail', '.topic-content', '.post-content', 'main',
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 100:
            return node
    return soup.body or soup


def _towiki_fetch_html_blocks(client, source_url):
    token = client.credentials.get('zsxq', {}).get('access_token', '')
    headers = {'User-Agent': 'Mozilla/5.0'}
    if token and 'zsxq.com' in source_url:
        headers['Cookie'] = f'zsxq_access_token={token}'
    resp = _towiki_get_with_retry(
        client, source_url, headers, 30, '源网页读取')
    soup = BeautifulSoup(resp.text, 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()

    blocks = []
    title = ''
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(' ', strip=True)
    if not title and soup.title:
        title = soup.title.get_text(' ', strip=True)
    if title:
        blocks.extend(_towiki_split_text_blocks(title, 3))

    content = _towiki_select_content(soup)
    seen_images = set()
    block_tags = ['h2', 'h3', 'h4', 'p', 'li', 'blockquote', 'pre', 'img']
    for el in content.find_all(block_tags):
        if any(parent.name in block_tags for parent in el.parents if parent is not content):
            continue
        if el.name == 'img':
            src = el.get('src') or el.get('data-src')
            if src and src not in seen_images:
                seen_images.add(src)
                blocks.append(_towiki_image_marker(src, source_url))
            continue

        block_type = {'h2': 4, 'h3': 5, 'h4': 6, 'li': 12}.get(el.name, 2)
        if el.name == 'pre':
            text = el.get_text('\n', strip=True)
            if text:
                blocks.extend(_towiki_split_text_blocks(text, 2))
        else:
            blocks.extend(_towiki_dom_to_blocks(el, block_type, source_url))
        for img in el.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if src and src not in seen_images:
                seen_images.add(src)
                blocks.append(_towiki_image_marker(src, source_url))

    if len(blocks) <= (1 if title else 0):
        text = content.get_text('\n', strip=True)
        blocks.extend(_towiki_split_text_blocks(text, 2))
    return blocks


def _towiki_source_is_pdf_url(source):
    parsed = urlparse(source)
    return parsed.path.lower().endswith('.pdf')


def _towiki_download_pdf_url(client, source_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = _towiki_get_with_retry(
        client, source_url, headers, 60, '源 PDF 下载')
    suffix = Path(urlparse(source_url).path).suffix or '.pdf'
    fd, temp_path = tempfile.mkstemp(prefix='goaipm_towiki_src_', suffix=suffix)
    with os.fdopen(fd, 'wb') as f:
        f.write(resp.content)
    return temp_path


def _towiki_pdf_data_with_pymupdf(pdf_path):
    try:
        import fitz
    except ImportError:
        return None

    pdf = fitz.open(str(pdf_path))
    out_dir = Path(tempfile.mkdtemp(prefix='goaipm_towiki_pdf_'))
    pages = []

    for page_index, page in enumerate(pdf, 1):
        backgrounds = _towiki_pdf_background_rects(page)
        links = []
        try:
            for lk in page.get_links():
                uri = lk.get('uri') or lk.get('file') or ''
                rect = lk.get('from')
                if uri and rect:
                    links.append({'uri': uri, 'rect': rect})
        except Exception:
            links = []

        raw = page.get_text('dict')
        lines = []
        for raw_index, block in enumerate(raw.get('blocks', [])):
            if block.get('type') != 0:
                continue
            for line in block.get('lines', []):
                spans = []
                text_parts = []
                x0 = y0 = x1 = y1 = None
                for span in line.get('spans', []):
                    txt = span.get('text', '')
                    if not txt:
                        continue
                    bbox = span.get('bbox') or [0, 0, 0, 0]
                    x0 = bbox[0] if x0 is None else min(x0, bbox[0])
                    y0 = bbox[1] if y0 is None else min(y0, bbox[1])
                    x1 = bbox[2] if x1 is None else max(x1, bbox[2])
                    y1 = bbox[3] if y1 is None else max(y1, bbox[3])
                    expanded_spans = _towiki_pdf_expand_span(span, links, backgrounds)
                    spans.extend(expanded_spans)
                    text_parts.append(txt)
                text = ''.join(text_parts).strip()
                if not text:
                    continue
                link = ''
                for lk in links:
                    rect = lk['rect']
                    if x0 is None or y0 is None or x1 is None or y1 is None:
                        continue
                    bbox = [x0, y0, x1, y1]
                    if (_towiki_pdf_rect_center_inside(bbox, rect) or
                            _towiki_pdf_rect_overlap_ratio(bbox, rect) >= 0.35):
                        link = lk['uri']
                        break
                lines.append({
                    'text': text,
                    'spans': spans,
                    'font': spans[0]['font'] if spans else '',
                    'size': max((s['size'] for s in spans), default=0),
                    'link': link,
                    'bbox': [x0 or 0, y0 or 0, x1 or 0, y1 or 0],
                    'block_index': raw_index,
                })

        items = []
        image_count = 0
        for raw_index, block in enumerate(raw.get('blocks', [])):
            bbox = _towiki_pdf_rect_list(block.get('bbox') or [0, 0, 0, 0])
            if block.get('type') == 0:
                item_lines = []
                for line in lines:
                    if line.get('block_index') == raw_index:
                        item_lines.append(line)
                if item_lines:
                    items.append({
                        'kind': 'text',
                        'bbox': bbox,
                        'index': raw_index,
                        'lines': item_lines,
                    })
                continue
            if block.get('type') != 1 or not block.get('image'):
                continue
            image_count += 1
            ext = block.get('ext') or 'png'
            name = f'page{page_index}_{image_count}.{ext}'
            out_path = out_dir / name
            out_path.write_bytes(block['image'])
            content_type = 'image/jpeg' if ext.lower() in ('jpg', 'jpeg') else f'image/{ext}'
            items.append({
                'kind': 'image',
                'bbox': bbox,
                'index': raw_index,
                'path': str(out_path),
                'name': name,
                'content_type': content_type,
            })

        items.sort(key=lambda item: (
            round((item.get('bbox') or [0, 0, 0, 0])[1], 1),
            round((item.get('bbox') or [0, 0, 0, 0])[0], 1),
            item.get('index', 0),
        ))

        pages.append({'lines': lines, 'items': items, 'images': []})

    pdf.close()
    return {'pages': pages}


def _towiki_pdf_blocks(source_path):
    pdf_path = Path(source_path).expanduser()
    if not pdf_path.exists():
        raise RuntimeError(f'PDF 文件不存在: {source_path}')

    bundled_data = _towiki_pdf_data_with_pymupdf(pdf_path)
    if bundled_data:
        docling_blocks = _towiki_pdf_blocks_with_docling(pdf_path, bundled_data)
        if docling_blocks:
            return docling_blocks

        blocks = _towiki_split_text_blocks(pdf_path.stem, 3)
        for page_index, page in enumerate(bundled_data.get('pages', []), 1):
            blocks.extend(_towiki_split_text_blocks(f'第 {page_index} 页', 4))
            items = page.get('items') or []
            if items:
                for item in items:
                    if item.get('kind') == 'image':
                        blocks.append(_towiki_image_marker(
                            item['path'],
                            filename=item.get('name', ''),
                            content_type=item.get('content_type', 'image/png'),
                        ))
                    else:
                        blocks.extend(_towiki_pdf_lines_to_blocks(item.get('lines', [])))
            else:
                blocks.extend(_towiki_pdf_lines_to_blocks(page.get('lines', [])))
                for image in page.get('images', []):
                    blocks.append(_towiki_image_marker(
                        image['path'],
                        filename=image.get('name', ''),
                        content_type=image.get('content_type', 'image/png'),
                    ))
        print(f'  PyMuPDF 回退解析成功：{len(blocks)} blocks', flush=True)
        return blocks
    return _towiki_split_text_blocks(pdf_path.stem, 3)


def process_towiki(client, source, target_url, write_retries=3):
    """--towiki 模式：把网页或 PDF 内容写入飞书 wiki/docx。"""
    if not _towiki_user_token(client):
        print('--towiki 最终失败：缺少飞书 user_access_token', flush=True)
        print('请运行 python src/modules/feishu_auth.py 完成用户授权',
              flush=True)
        print('目标文档未修改', flush=True)
        return False
    print(f'源内容: {source}', flush=True)
    print(f'目标文档: {target_url}', flush=True)
    try:
        doc_id = _towiki_resolve_doc_id(client, target_url)
    except Exception as error:
        print(f'目标文档访问失败：{error}', flush=True)
        print('请检查目标 URL、飞书用户授权及文档编辑权限', flush=True)
        print('目标文档未修改', flush=True)
        return False
    print(f'目标 document_id: {doc_id}', flush=True)

    try:
        if (source.lower().startswith(('http://', 'https://'))
                and _towiki_source_is_pdf_url(source)):
            print('下载源 PDF...', flush=True)
            pdf_path = _towiki_download_pdf_url(client, source)
            blocks = _towiki_pdf_blocks(pdf_path)
        elif source.lower().startswith(('http://', 'https://')):
            print('读取源网页...', flush=True)
            blocks = _towiki_fetch_html_blocks(client, source)
        else:
            print('读取源 PDF...', flush=True)
            blocks = _towiki_pdf_blocks(source)
    except Exception as error:
        print(f'源内容读取最终失败：{error}', flush=True)
        print('目标文档尚未清空或写入', flush=True)
        return False

    if not blocks:
        print('源内容解析失败：未提取到可写入的 blocks', flush=True)
        print('目标文档尚未清空或写入', flush=True)
        return False
    print(f'提取到 {len(blocks)} 个待写入 blocks', flush=True)

    for attempt in range(1, write_retries + 1):
        last_reported = 0

        def report_progress(written):
            nonlocal last_reported
            if written == len(blocks) or written - last_reported >= 100:
                print(f'  写入进度: {written}/{len(blocks)} blocks',
                      flush=True)
                last_reported = written

        try:
            print(f'清空目标文档（尝试 {attempt}/{write_retries}）...',
                  flush=True)
            _towiki_clear_document(client, doc_id)
            print(f'写入目标文档（尝试 {attempt}/{write_retries}）...',
                  flush=True)
            _towiki_append_blocks(
                client, doc_id, blocks, progress_callback=report_progress)
        except Exception as error:
            retryable = _towiki_is_transient_error(error)
            if retryable and attempt < write_retries:
                wait_seconds = 3 * attempt
                print(f'整份写入失败（尝试 {attempt}/{write_retries}）：'
                      f'{error}', flush=True)
                print(f'{wait_seconds} 秒后将重新清空并从头写入',
                      flush=True)
                time.sleep(wait_seconds)
                continue
            print(f'整份写入最终失败（尝试 {attempt}/{write_retries}）：'
                  f'{error}', flush=True)
            if not retryable:
                print('该错误不适合自动重试，请检查输入、凭证或文档权限',
                      flush=True)
            print('目标文档可能只写入了部分内容，请排除故障后重新运行命令',
                  flush=True)
            return False

        if attempt > 1:
            print(f'整份文档重试成功（尝试 {attempt}/{write_retries}）',
                  flush=True)
        print(f'目标文档写入完成：{len(blocks)} blocks', flush=True)
        return True

    return False


def process_one_doc(client, parser, config, doc_token, doc_url, doc_index, total_docs):
    """处理单个周报文档：提取 URL → 解析 → 日报核对 → 写入多维表格

    Returns:
        dict: {urls: int, parsed: int, errors: int, written: int}
    """
    tag = f'[{doc_index}/{total_docs}]'

    # 阶段一：提取周报 URL
    print(f'\n{tag} 阶段一：提取 URL', flush=True)
    url_items = extract_urls_from_doc(client, doc_token)
    print(f'{tag}   提取到 {len(url_items)} 个 URL', flush=True)

    if not url_items:
        print(f'{tag}   无 URL，跳过')
        return {'urls': 0, 'parsed': 0, 'errors': 0, 'written': 0}, []

    weekly_time = url_items[0]['weekly_time']

    # 写临时文件（按文档编号区分）
    urls_file = os.path.join(AIPM_DIR, f'aipm_weekly_urls_{doc_index}.csv')
    parsed_file = os.path.join(AIPM_DIR, f'aipm_weekly_parsed_{doc_index}.csv')
    write_urls_csv(url_items, urls_file)

    # 阶段二：解析周报 URL
    print(f'\n{tag} 阶段二：解析 URL', flush=True)
    parsed_rows, error_rows = parse_urls_phase2(url_items, parser, client)
    write_parsed_csv(parsed_rows, parsed_file)
    if error_rows:
        write_error_log(error_rows, ERROR_LOG_FILE)

    # 阶段三：日报核对
    zsxq_token = parser.credentials.get('zsxq_token', '')
    if zsxq_token:
        print(f'\n{tag} 阶段三：日报核对', flush=True)
        wr_cfg = config.get('weekly_report', {})
        exclude_urls = wr_cfg.get('daily_exclude_urls', [])
        new_daily_items, daily_errors = process_daily_phase(
            client, parser, parsed_rows, url_items,
            doc_token, doc_url, weekly_time, zsxq_token, exclude_urls)
        url_items.extend(new_daily_items)
        error_rows.extend(daily_errors)
        if daily_errors:
            write_error_log(daily_errors, ERROR_LOG_FILE)
        # 重写 parsed CSV（含日报更新）
        write_parsed_csv(parsed_rows, parsed_file)
    else:
        print(f'{tag} 跳过日报核对（未配置 zsxq token）')

    # 阶段四：写入多维表格
    print(f'\n{tag} 阶段四：写入多维表格', flush=True)
    success, cache_entries = write_to_bitable(client, parsed_rows, config)

    stats = {
        'urls': len(url_items),
        'parsed': len(parsed_rows),
        'errors': len(error_rows),
        'written': len(parsed_rows),
    }
    print(f'\n{tag} 完成 (URL:{stats["urls"]} 解析:{stats["parsed"]} '
          f'异常:{stats["errors"]})')
    return stats, cache_entries


def main():
    """主函数"""
    _setup_encoding()

    # 同步输出到日志文件
    log_path = os.path.join(PROJECT_ROOT, 'log-err', 'aipm_output.txt')

    class Tee:
        def __init__(self, stream, filepath):
            self._stream = stream
            self._file = open(filepath, 'a', encoding='utf-8')
        def write(self, data):
            self._stream.write(data)
            self._file.write(data)
        def flush(self):
            self._stream.flush()
            self._file.flush()
        def __getattr__(self, attr):
            return getattr(self._stream, attr)

    sys.stdout = Tee(sys.stdout, log_path)
    sys.stderr = Tee(sys.stderr, log_path)

    ap = argparse.ArgumentParser(description='星球周报 → 飞书多维表格')
    group = ap.add_mutually_exclusive_group()
    group.add_argument('--file', metavar='<url>', help='单个周报文档 URL')
    group.add_argument('--list', metavar='<listfile>', help='周报文档列表文件路径')
    group.add_argument('--daily', metavar='<url>', help='单个日报文档 URL（zsxq 短链或直链）')
    group.add_argument('--update', action='store_true', help='自动处理新日报（基于 last_processed_date）')
    group.add_argument('--weekly', metavar='<url>', help='基于周报 wiki 完善多维表格')
    group.add_argument('--towiki', nargs=2, metavar=('<src>', '<dst>'),
                       help='把源网页 URL 或 PDF 文件内容写入目标飞书 wiki/docx')
    args = ap.parse_args()

    print('=' * 60, flush=True)
    print('星球周报 → 飞书多维表格')
    print('=' * 60, flush=True)

    client = FeishuClient()
    feishu_auth = client.credentials.get('auth_feishuMSG-xls',
                                         client.credentials.get('auth', {}))
    credentials = {
        'zsxq_token': client.credentials.get('zsxq', {}).get('access_token', ''),
        'zhihu_cookies': client.credentials.get('zhihu', {}),
        'feishu_user_token': feishu_auth.get('user_access_token', ''),
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

    config = client.config

    # 确定文档 URL 列表
    if args.file:
        doc_urls = [args.file.strip()]
    elif args.list:
        list_path = args.list
        if not os.path.isabs(list_path):
            list_path = os.path.join(PROJECT_ROOT, list_path)
        with open(list_path, 'r', encoding='utf-8') as f:
            doc_urls = [line.strip() for line in f if line.strip()]
    elif args.daily:
        zsxq_token = credentials['zsxq_token']
        if not zsxq_token:
            print('--daily 需要 zsxq token，请检查 '
                  '~/.config/secrets/gtokens.yaml')
            return
        bt_cfg = config.get('weekly_report', {}).get('target_bitable', {})
        bt_table_id = bt_cfg.get('table_id', '')
        bc = None
        if bt_table_id:
            bc = BitableUrlCache(bt_table_id, DATA_DIR)
            bc.load()
        process_daily_standalone(client, parser, config, args.daily.strip(),
                                zsxq_token, bc)
        return
    elif args.update:
        process_daily_update(client, parser, config, credentials)
        return
    elif args.weekly:
        process_weekly(client, parser, config, args.weekly.strip())
        return
    elif args.towiki:
        source, target_url = args.towiki
        if not process_towiki(client, source.strip(), target_url.strip()):
            raise SystemExit(1)
        return
    else:
        print('请指定 --file / --list / --daily / --update / --weekly / --towiki')
        return

    # 提取 doc_token，保留原始 URL
    doc_entries = []
    for url in doc_urls:
        m = re.search(r'/wiki/([A-Za-z0-9]+)', url)
        if m:
            doc_entries.append((m.group(1), url))
        else:
            print(f'  无法解析 doc_token: {url}')

    total = len(doc_entries)
    print(f'\n周报文档: {total} 篇')

    # 初始化 bitable URL 缓存
    bt_cfg = config.get('weekly_report', {}).get('target_bitable', {})
    bt_table_id = bt_cfg.get('table_id', '')
    bitable_cache = None
    if bt_table_id:
        bitable_cache = BitableUrlCache(bt_table_id, DATA_DIR)
        bt_urls, _ = bitable_cache.load()
        print(f'\nbitable_url_cache 已加载 {len(bt_urls)} 条', flush=True)

    # 逐个文档处理
    all_stats = []
    for i, (doc_token, doc_url) in enumerate(doc_entries, 1):
        print(f'\n{"=" * 60}')
        print(f'文档 {i}/{total}: {doc_token}')
        print('=' * 60)
        stats, cache_entries = process_one_doc(
            client, parser, config, doc_token, doc_url, i, total)
        all_stats.append(stats)
        if bitable_cache and cache_entries:
            bitable_cache.append(cache_entries)
            print(f'  追加 {len(cache_entries)} 条 → {os.path.basename(bitable_cache._file)}')

    # 汇总
    print(f'\n{"=" * 60}')
    print('全部处理完成')
    print('=' * 60)
    total_urls = sum(s['urls'] for s in all_stats)
    total_errors = sum(s['errors'] for s in all_stats)
    print(f'  文档: {total} 篇')
    print(f'  URL: {total_urls} 条')
    print(f'  异常: {total_errors} 条')


if __name__ == '__main__':
    main()
