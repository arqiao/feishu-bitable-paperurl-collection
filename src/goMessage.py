"""
飞书群消息整理主程序
自动读取飞书群消息，提取网络链接，解析文章信息，写入飞书多维表格。

用法:
  python src/goMessage.py --profile ai       # 指定 profile 运行
  python src/goMessage.py --profile ot       # 指定 ot profile
  python src/goMessage.py --all              # 全量拉取（忽略 last_processed_time）
  python src/goMessage.py --reset            # 重置处理时间
  python src/goMessage.py --list-nolink      # 列出无链接消息
  python src/goMessage.py --start T --end T  # 指定时间戳范围（毫秒）

参数:
  --profile NAME     指定 config.yaml 中的 profile 名称（ai/ot）
  --all              全量拉取，不受 last_processed_time 限制
  --reset            重置 last_processed_time 为 0
  --start TIMESTAMP  起始时间戳（毫秒）
  --end TIMESTAMP    终止时间戳（毫秒）
  --list-nolink      列出无链接的消息

输出文件:
  log-err/msg_index_cache.json       消息索引缓存
  log-err/msg_log.csv                处理日志（GBK 编码）
  log-err/msg_duplicate_log.csv      重复 URL 日志
  log-err/msg_parse_error_log.csv    解析失败日志
  log-err/msg_bitable_fail_log.csv   多维表格写入失败日志
"""

import csv
import json
import os
import re
import time
import argparse
import sys
import io
from datetime import datetime

from modules.bitable_url_cache import BitableUrlCache
from modules.config_utils import (
    format_unix_ts_comment,
    set_config_value_preserve_comments,
)
from url_parser import normalize_url


# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 日志与数据目录
LOG_ERR_DIR = os.path.join(PROJECT_ROOT, 'log-err')
os.makedirs(LOG_ERR_DIR, exist_ok=True)
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# 消息序号缓存文件
CACHE_FILE = os.path.join(LOG_ERR_DIR, 'msg_index_cache.json')

# CSV 日志文件
CSV_FILE = os.path.join(LOG_ERR_DIR, 'msg_log.csv')
CSV_HEADERS = [
    '标题', '日期', '星期', '链接', '来源',
    '标记', '是否重复', '消息序号', '摘录异常信息', '备注',
]

# 特殊事件日志文件
DUPLICATE_LOG_FILE = os.path.join(LOG_ERR_DIR, 'msg_duplicate_log.csv')
PARSE_ERROR_LOG_FILE = os.path.join(LOG_ERR_DIR, 'msg_parse_error_log.csv')
BITABLE_FAIL_LOG_FILE = os.path.join(LOG_ERR_DIR, 'msg_bitable_fail_log.csv')

# 设置标准输出编码为 UTF-8
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def _config_path_for_profile(client, *keys):
    profile_name = getattr(client, '_profile_name', None)
    if profile_name:
        return ['feishuMessage', profile_name, *keys]
    return list(keys)

def load_index_cache(chat_id: str) -> dict:
    """加载消息序号缓存，chat_id 不匹配或文件损坏时返回空缓存"""
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        if cache.get('chat_id') == chat_id:
            return cache
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return {'chat_id': chat_id, 'next_index': 1, 'mapping': {}}


def save_index_cache(cache: dict):
    """保存消息序号缓存到文件"""
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)


def load_existing_urls_from_csv() -> set:
    """从 CSV 文件读取已有的文章链接URL集合（用于重复检测）"""
    urls = set()
    if not os.path.exists(CSV_FILE):
        return urls
    try:
        with open(CSV_FILE, 'r', encoding='gbk') as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get('链接', '').strip()
                if url:
                    urls.add(normalize_url(url))
    except Exception:
        pass
    return urls


def sanitize_for_gbk(value):
    """过滤无法用 GBK 编码的字符（emoji、零宽字符等）"""
    if not isinstance(value, str):
        return value
    try:
        value.encode('gbk')
        return value
    except UnicodeEncodeError:
        # 逐字符检查，保留能用 GBK 编码的字符
        result = []
        for c in value:
            try:
                c.encode('gbk')
                result.append(c)
            except UnicodeEncodeError:
                pass
        return ''.join(result)


def append_rows_to_csv(rows: list):
    """追加数据行到 CSV 文件，不存在则自动创建含表头的文件（GBK）"""
    # 过滤无法用 GBK 编码的字符
    cleaned_rows = [[sanitize_for_gbk(cell) for cell in row] for row in rows]
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='gbk') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADERS)
        writer.writerows(cleaned_rows)


def append_duplicate_log(rows: list):
    """追加重复数据记录到日志文件"""
    if not rows:
        return
    cleaned_rows = [[sanitize_for_gbk(cell) for cell in row] for row in rows]
    headers = ['标题', '日期', '星期', '链接', '来源', '标记', '消息序号', '记录时间']
    file_exists = os.path.exists(DUPLICATE_LOG_FILE)
    with open(DUPLICATE_LOG_FILE, 'a', newline='', encoding='gbk') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerows(cleaned_rows)


def append_parse_error_log(rows: list):
    """追加解析异常记录到日志文件"""
    if not rows:
        return
    cleaned_rows = [[sanitize_for_gbk(cell) for cell in row] for row in rows]
    headers = ['标题', '日期', '星期', '链接', '来源', '标记', '异常信息', '消息序号', '记录时间']
    file_exists = os.path.exists(PARSE_ERROR_LOG_FILE)
    with open(PARSE_ERROR_LOG_FILE, 'a', newline='', encoding='gbk') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerows(cleaned_rows)


def append_bitable_fail_log(rows: list):
    """追加多维表格入库失败记录到日志文件"""
    if not rows:
        return
    cleaned_rows = [[sanitize_for_gbk(cell) for cell in row] for row in rows]
    headers = ['标题', '日期', '星期', '链接', '来源', '标记', '失败原因', '消息序号', '记录时间']
    file_exists = os.path.exists(BITABLE_FAIL_LOG_FILE)
    with open(BITABLE_FAIL_LOG_FILE, 'a', newline='', encoding='gbk') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerows(cleaned_rows)


def extract_urls_from_message(message: dict, show_errors: bool = False) -> list:
    """从消息中提取 URL

    Args:
        message: 消息对象
        show_errors: 是否显示解析错误信息

    Returns:
        list: URL 列表
    """
    urls = []

    # 获取消息内容
    content = message.get('body', {}).get('content', '')
    if not content:
        return urls

    try:
        # 解析 JSON 内容
        content_json = json.loads(content)

        # 文本消息
        if message.get('msg_type') == 'text':
            text = content_json.get('text', '')
            # 使用正则提取 URL
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
            found_urls = re.findall(url_pattern, text)
            urls.extend(found_urls)

        # 富文本消息
        elif message.get('msg_type') == 'post':
            post_content = content_json.get('content', {})
            for lang_content in post_content.values():
                for item in lang_content:
                    for element in item:
                        if element.get('tag') == 'a':
                            href = element.get('href', '')
                            if href:
                                urls.append(href)
                        elif element.get('tag') == 'text':
                            text = element.get('text', '')
                            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
                            found_urls = re.findall(url_pattern, text)
                            urls.extend(found_urls)

    except Exception as e:
        if show_errors:
            print(f"解析消息内容失败: {e}", flush=True)

    return urls


def get_message_text(message: dict) -> str:
    """获取消息的文本内容"""
    content = message.get('body', {}).get('content', '')
    if not content:
        return ''

    try:
        content_json = json.loads(content)

        # 文本消息
        if message.get('msg_type') == 'text':
            return content_json.get('text', '')

        # 富文本消息
        elif message.get('msg_type') == 'post':
            text_parts = []
            post_content = content_json.get('content', {})
            for lang_content in post_content.values():
                for item in lang_content:
                    for element in item:
                        if element.get('tag') == 'text':
                            text_parts.append(element.get('text', ''))
                        elif element.get('tag') == 'a':
                            text_parts.append(element.get('text', ''))
            return ' '.join(text_parts)

        # 其他类型消息
        else:
            return f"[{message.get('msg_type', 'unknown')} 类型消息]"

    except Exception as e:
        return f"[解析失败: {e}]"


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='飞书群消息整理工具')
    parser.add_argument('--all', action='store_true',
                        help='处理所有历史消息（默认只处理新消息）')
    parser.add_argument('--reset', action='store_true',
                        help='重置处理时间，从头开始处理所有消息')
    parser.add_argument('--start', type=int, default=None,
                        help='起始消息索引（从1开始）')
    parser.add_argument('--end', type=int, default=None,
                        help='结束消息索引（包含）')
    parser.add_argument('--list-nolink', action='store_true',
                        help='显示未含链接的消息清单')
    parser.add_argument('--profile', type=str, default=None,
                        help='指定配置 profile 名称（对应 config.yaml 中 profiles 下的键）')
    args = parser.parse_args()

    print("=" * 60, flush=True)
    print("飞书群消息整理工具", flush=True)
    print("=" * 60, flush=True)

    # 延迟导入重型模块（requests ~600ms, bs4 ~200ms）
    from feishu_client import FeishuClient
    from url_parser import UrlParser

    # 初始化客户端
    client = FeishuClient()
    parser = UrlParser()

    # 加载 profile 配置
    profiles = client.config.get('feishuMessage', {})
    if profiles:
        profile_names = list(profiles.keys())
        if args.profile:
            if args.profile not in profiles:
                print(f"\n✗ 未找到 profile: {args.profile}，可用: {', '.join(profile_names)}", flush=True)
                return
            profile_name = args.profile
        else:
            profile_name = profile_names[0]
            print(f"\n未指定 --profile，使用默认: {profile_name}", flush=True)
        profile_cfg = profiles[profile_name]
        # 将 profile 内容合并到顶层，覆盖旧的平铺字段
        client.config['target_chat'] = profile_cfg['target_chat']
        client.config['target_bitable'] = profile_cfg['target_bitable']
        client.config['last_processed_time'] = profile_cfg.get('last_processed_time', 0)
        client._profile_name = profile_name  # 保存 profile 名，供保存时使用
    else:
        client._profile_name = None

    # 检查 token 是否有效
    if not client.check_token_valid():
        print("\nToken 已过期或无效，尝试刷新...", flush=True)
        if not client.refresh_access_token():
            print("\n✗ Token 刷新失败，请重新授权：", flush=True)
            print("  运行命令: python src/modules/feishu_auth.py", flush=True)
            return

    print("\n✓ Token 验证成功", flush=True)

    # 获取或查找群聊 ID
    chat_id = client.config['target_chat'].get('chat_id', '')
    if not chat_id:
        print("\n正在查找目标群聊...", flush=True)
        chat_name = client.config['target_chat']['name']
        chat_id = client.find_chat_by_name(chat_name)

        if not chat_id:
            print(f"\n✗ 未找到群聊: {chat_name}", flush=True)
            print("  请检查群聊名称是否正确", flush=True)
            return

        # 保存群聊 ID
        client.config['target_chat']['chat_id'] = chat_id
        set_config_value_preserve_comments(
            client.config_path, _config_path_for_profile(client, 'target_chat', 'chat_id'), chat_id)
        print(f"✓ 找到群聊: {chat_name} (ID: {chat_id})", flush=True)
    else:
        print(f"\n✓ 使用已配置的群聊 ID: {chat_id}", flush=True)

    # 获取上次处理时间
    last_processed_time = client.config.get('last_processed_time', 0)

    # 根据命令行参数决定是否重置时间
    if args.reset:
        last_processed_time = 0
        client.config['last_processed_time'] = 0
        set_config_value_preserve_comments(
            client.config_path,
            _config_path_for_profile(client, 'last_processed_time'),
            0,
            comment='19700101-00:00',
        )
        print("\n✓ 已重置处理时间，将处理所有历史消息", flush=True)
    elif args.all:
        last_processed_time = 0
        print("\n✓ 使用 --all 参数，将处理所有历史消息", flush=True)
    else:
        if last_processed_time > 0:
            last_time_str = datetime.fromtimestamp(last_processed_time/1000).strftime('%Y-%m-%d %H:%M:%S')
            print(f"\n上次处理时间: {last_time_str}", flush=True)
        else:
            print(f"\n上次处理时间: 首次运行", flush=True)

    # 获取消息并分配序号
    print("\n正在获取群聊消息...", flush=True)
    if args.reset or args.all:
        # 全量模式：拉取所有消息，重建缓存
        all_messages = client.get_chat_messages(chat_id, start_time=0)
        cache = {'chat_id': chat_id, 'next_index': 1, 'mapping': {}}
        for msg in all_messages:
            mid = msg.get('message_id', '')
            msg['original_index'] = cache['next_index']
            cache['mapping'][mid] = cache['next_index']
            cache['next_index'] += 1
        save_index_cache(cache)
        total_count = len(all_messages)
        messages = all_messages
        new_count = total_count
        print(f"✓ 消息总数: {total_count} 条（缓存已重建）", flush=True)
    else:
        # 增量模式：加载缓存，只拉取新消息
        cache = load_index_cache(chat_id)
        fetch_start = last_processed_time if cache['mapping'] else 0
        fetched = client.get_chat_messages(chat_id, start_time=fetch_start)
        total_count = len(cache['mapping'])
        new_msgs = []
        for msg in fetched:
            mid = msg.get('message_id', '')
            if mid in cache['mapping']:
                msg['original_index'] = cache['mapping'][mid]
            else:
                msg['original_index'] = cache['next_index']
                cache['mapping'][mid] = cache['next_index']
                cache['next_index'] += 1
                total_count += 1
            # 只保留新消息（create_time > last_processed_time）
            if int(msg.get('create_time', '0')) > last_processed_time:
                new_msgs.append(msg)
        save_index_cache(cache)
        messages = new_msgs
        new_count = len(messages)

        if last_processed_time > 0:
            last_time_str = datetime.fromtimestamp(last_processed_time/1000).strftime('%H:%M')
            print(f"✓ 消息总数: {total_count} 条，其中自 {last_time_str} 以后的新消息有 {new_count} 条", flush=True)
        else:
            print(f"✓ 消息总数: {total_count} 条（首次运行，全部为新消息）", flush=True)

    if new_count == 0:
        print("\n没有新消息需要处理", flush=True)
        return

    # 提取 URL 并记录消息序号
    print("\n正在提取消息中的链接...", flush=True)
    url_messages = []
    messages_without_links = []

    # 在 --list-nolink 模式下显示解析错误
    show_parse_errors = args.list_nolink

    for msg in messages:
        # 跳过 bot 消息（sender_type 为 app）
        sender_type = msg.get('sender', {}).get('sender_type', '')
        if sender_type == 'app':
            continue
        original_idx = msg['original_index']  # 使用原始序号
        urls = extract_urls_from_message(msg, show_errors=show_parse_errors)
        if urls:
            for url in urls:
                url_messages.append({
                    'url': url,
                    'message_time': int(msg.get('create_time', '0')),
                    'message_index': original_idx,  # 使用原始序号
                    'message_id': msg.get('message_id', '')
                })
        else:
            # 记录没有链接的消息
            message_text = get_message_text(msg)
            messages_without_links.append({
                'index': original_idx,  # 使用原始序号
                'text': message_text,
                'time': datetime.fromtimestamp(int(msg.get('create_time', '0')) / 1000)
            })

    print(f"✓ 提取到 {len(url_messages)} 个链接", flush=True)

    # 显示未含链接的消息清单（仅在指定参数时显示）
    if args.list_nolink:
        if messages_without_links:
            print(f"\n{'='*60}", flush=True)
            print(f"未含链接的消息清单（共 {len(messages_without_links)} 条）", flush=True)
            print(f"{'='*60}", flush=True)
            for msg_info in messages_without_links:
                print(f"\n[消息 {msg_info['index']}] 时间: {msg_info['time']}", flush=True)
                # 限制显示长度
                text = msg_info['text']
                if len(text) > 100:
                    text = text[:100] + "..."
                print(f"  内容: {text}", flush=True)
            print(f"\n{'='*60}\n", flush=True)
        else:
            print(f"\n所有消息都包含链接", flush=True)

        # --list-nolink 模式下，显示完消息后直接退出，不进行后续处理
        print("✓ 列出完成，不进行后续链接解析和存储", flush=True)
        return
    elif messages_without_links:
        print(f"✓ 发现 {len(messages_without_links)} 条未含链接的消息（使用 --list-nolink 参数可查看详情）", flush=True)

    # 应用范围参数
    if args.start or args.end:
        start_idx = (args.start - 1) if args.start else 0
        end_idx = args.end if args.end else len(url_messages)
        url_messages = url_messages[start_idx:end_idx]
        print(f"✓ 应用范围参数，处理第 {start_idx + 1} 到第 {end_idx} 条链接，共 {len(url_messages)} 条", flush=True)

    if len(url_messages) == 0:
        print("\n没有包含链接的消息", flush=True)
        # 更新最后处理时间
        if messages:
            latest_time = max(int(msg.get('create_time', '0')) for msg in messages)
            client.config['last_processed_time'] = latest_time
            set_config_value_preserve_comments(
                client.config_path,
                _config_path_for_profile(client, 'last_processed_time'),
                latest_time,
                comment=format_unix_ts_comment(latest_time / 1000),
            )
            latest_time_str = datetime.fromtimestamp(latest_time/1000).strftime('%Y-%m-%d %H:%M:%S')
            print(f"✓ 已更新处理时间: {latest_time_str}", flush=True)
        return

    # 从本地 CSV 读取已有 URL（用于重复检测）
    print("\n正在读取本地 CSV 已有 URL...", flush=True)
    existing_urls = load_existing_urls_from_csv()
    print(f"✓ CSV 中已有 {len(existing_urls)} 条 URL 记录", flush=True)

    # 多维表格初始化（默认执行）
    bitable_app_token = None
    bitable_table_id = None
    bitable_cache = None
    bitable_existing_urls = set()
    bitable_url_record_map = {}  # URL -> record_id，用于更新已有记录
    bitable_col_cfg = client.config.get('bitable_columns', {})
    bitable_cfg = client.config.get('target_bitable', {})
    sort_cfg = client.config.get('sort_config', {})

    if bitable_cfg.get('app_token'):
        bitable_table_id = bitable_cfg.get('table_id', '')
        bitable_app_token = bitable_cfg.get('app_token', '')

        # 读取多维表格已有 URL 用于去重（本地缓存）
        if bitable_app_token and bitable_table_id:
            url_field = bitable_col_cfg.get('url', '链接')
            bitable_cache = BitableUrlCache(bitable_table_id, DATA_DIR)
            bitable_existing_urls, bitable_url_record_map = bitable_cache.load()
            if bitable_cache.is_empty():
                bitable_cache.rebuild(client, bitable_app_token,
                                      bitable_table_id, url_field)
                bitable_existing_urls, bitable_url_record_map = bitable_cache.load()
            print(f"✓ 多维表格缓存中已有 {len(bitable_existing_urls)} 条记录", flush=True)

    # 获取 Pin 消息列表
    print("\n正在获取 Pin 消息列表...", flush=True)
    pin_message_ids = set(client.get_pin_messages(chat_id))
    print(f"✓ 群内共有 {len(pin_message_ids)} 条 Pin 消息", flush=True)

    # 处理每个链接
    print("\n开始处理链接...", flush=True)
    success_count = 0
    error_count = 0
    csv_rows = []
    bitable_rows = []
    bitable_skipped_count = 0  # 因已存在于多维表格而跳过的记录数
    bitable_pin_updates = []  # 已在多维表格中的 Pin 消息，需更新兴趣优先级
    duplicate_rows = []
    parse_error_rows = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for idx, item in enumerate(url_messages, 1):
        url = item['url']
        message_index = item['message_index']
        message_id = item['message_id']
        print(f"\n[{idx}/{len(url_messages)}] 处理: {url}", flush=True)
        print(f"  消息序号: {message_index}", flush=True)

        # 解析文章
        article_info = parser.parse_url(url)
        print(f"  标题: {article_info['title'][:50] if article_info['title'] else '未获取'}", flush=True)
        print(f"  来源: {article_info['source'] if article_info['source'] else '未识别'}", flush=True)
        print(f"  日期: {article_info['publish_date'] if article_info['publish_date'] else '未获取'}", flush=True)

        # 判断是否重复（解析后的 URL 与已有 URL 比对）
        parsed_norm = normalize_url(article_info['url'])
        is_duplicate = parsed_norm in existing_urls
        if is_duplicate:
            print(f"  重复: 是", flush=True)

        # 判断是否为 Pin 消息
        is_pinned = message_id in pin_message_ids
        if is_pinned:
            print(f"  标记: Pin", flush=True)

        # 构建 CSV 行数据（按 CSV_HEADERS 固定顺序）
        csv_row = [
            article_info['title'],                          # 文章标题
            article_info['publish_date'],                   # 文章发表日期
            article_info['weekday'],                        # 星期
            article_info['url'],                            # 文章链接URL
            article_info['source'],                         # 文章来源
            'Pin' if is_pinned else '',                     # 标记
            '重复' if is_duplicate else '',                 # 是否重复
            str(message_index),                             # 消息序号
            article_info['error_info'],                     # 摘录异常信息
            article_info['remark'],                         # 备注
        ]

        # 将当前 URL 加入已有集合（同批次内也检测重复）
        existing_urls.add(parsed_norm)

        # 记录解析后的 URL 和是否有解析错误（用于撤回时判断）
        item['parsed_url'] = article_info['url']
        item['has_error'] = bool(article_info['error_info'])

        csv_rows.append(csv_row)
        success_count += 1

        # 收集多维表格数据（不在多维表格中 且 解析成功的记录才写入）
        if parsed_norm not in bitable_existing_urls and not article_info['error_info']:
            bitable_rows.append({
                'title': article_info['title'],
                'publish_date': article_info['publish_date'],
                'weekday': article_info['weekday'],
                'url': article_info['url'],
                'source': article_info['source'],
                'message_index': message_index,
                'is_pinned': is_pinned,
            })
            bitable_existing_urls.add(parsed_norm)
        elif is_pinned and parsed_norm in bitable_url_record_map:
            # Pin 消息已在多维表格中，收集待更新记录
            bitable_pin_updates.append({
                'record_id': bitable_url_record_map[parsed_norm],
                'url': article_info['url'],
            })
        elif parsed_norm in bitable_existing_urls and not article_info['error_info']:
            # URL 已存在于多维表格，跳过写入
            bitable_skipped_count += 1

        # 收集重复记录日志
        if is_duplicate:
            duplicate_rows.append([
                article_info['title'],
                article_info['publish_date'],
                article_info['weekday'],
                article_info['url'],
                article_info['source'],
                'Pin' if is_pinned else '',
                str(message_index),
                now_str,
            ])

        # 收集解析异常记录日志（使用截断前的原始 URL）
        if article_info['error_info']:
            parse_error_rows.append([
                article_info['title'],
                article_info['publish_date'],
                article_info['weekday'],
                url,
                article_info['source'],
                'Pin' if is_pinned else '',
                article_info['error_info'],
                str(message_index),
                now_str,
            ])

        # 避免请求过快
        time.sleep(1)

    # 写入本地 CSV
    if csv_rows:
        print(f"\n正在将 {len(csv_rows)} 条记录写入 CSV...", flush=True)
        append_rows_to_csv(csv_rows)
        print(f"✓ 数据已写入 {CSV_FILE}", flush=True)

    # 写入特殊事件日志
    if duplicate_rows:
        append_duplicate_log(duplicate_rows)
        print(f"✓ 重复记录日志: {len(duplicate_rows)} 条 -> {DUPLICATE_LOG_FILE}", flush=True)
    if parse_error_rows:
        append_parse_error_log(parse_error_rows)
        print(f"✓ 解析异常日志: {len(parse_error_rows)} 条 -> {PARSE_ERROR_LOG_FILE}", flush=True)

    # 写入多维表格（默认执行）
    bitable_write_ok = False
    bitable_available = bool(bitable_app_token and bitable_table_id)
    bitable_fail_rows = []  # 收集入库失败记录
    now_str = datetime.now().strftime('%Y/%m/%d %H:%M')

    if bitable_available and bitable_rows:
        # 按 publish_date 排序
        bitable_rows.sort(key=lambda r: r.get('publish_date', '') or '')
        print(f"\n正在将 {len(bitable_rows)} 条非重复记录批量写入多维表格...", flush=True)

        # 构建批量写入的 records 列表
        batch_records = []
        for row in bitable_rows:
            fields = {}
            for key in ('title', 'publish_date', 'weekday', 'url', 'source'):
                col_name = bitable_col_cfg.get(key, '')
                if col_name and row.get(key):
                    value = row[key]
                    # 日期字段在多维表格中是数字类型，存储为 YYYYMMDD 整数
                    if key == 'publish_date' and isinstance(value, str) and value.isdigit():
                        value = int(value)
                    fields[col_name] = value
            # Pin 消息写入"兴趣优先级"列
            if row.get('is_pinned'):
                priority_field = sort_cfg.get('priority_field', '兴趣优先级')
                fields[priority_field] = '兴趣D'
            if fields:
                batch_records.append(fields)

        # 批量写入（单次 API 调用，最多 500 条）
        if batch_records:
            result = client.batch_add_bitable_records(bitable_app_token, bitable_table_id, batch_records)
            bt_ok = result['success']
            bt_fail = result['failed']

            # 追加新记录到本地缓存
            if bitable_cache and bt_ok > 0:
                created = result.get('records', [])
                cache_entries = []
                for cr in created:
                    rid = cr.get('record_id', '')
                    fields = cr.get('fields', {})
                    url_col = bitable_col_cfg.get('url', '链接')
                    url_val = fields.get(url_col, '')
                    if isinstance(url_val, dict):
                        url_val = url_val.get('link', '') or url_val.get('text', '')
                    if url_val:
                        cache_entries.append({'url': str(url_val).strip(),
                                              'record_id': rid})
                if cache_entries:
                    bitable_cache.append(cache_entries)
                    print(f"  追加 {len(cache_entries)} 条 → {os.path.basename(bitable_cache._file)}")

            if bt_fail > 0:
                # 记录失败信息
                error_msg = result['errors'][0] if result['errors'] else 'API写入失败'
                print(f"  写入失败详情: {error_msg}", flush=True)
                for row in bitable_rows:
                    bitable_fail_rows.append([
                        row.get('title', ''),
                        row.get('publish_date', ''),
                        row.get('weekday', ''),
                        row.get('url', ''),
                        row.get('source', ''),
                        'Pin' if row.get('is_pinned') else '',
                        error_msg if len(str(error_msg)) < 100 else str(error_msg)[:97] + '...',
                        row.get('message_index', ''),
                        now_str
                    ])

            print(f"✓ 多维表格批量写入完成: 成功 {bt_ok} 条, 失败 {bt_fail} 条", flush=True)
            bitable_write_ok = (bt_fail == 0)
        else:
            print("✓ 无有效记录需要写入多维表格", flush=True)
            bitable_write_ok = True
    elif not bitable_available and bitable_rows:
        # 多维表格不可用，记录所有应写入的记录
        print(f"\n⚠ 多维表格配置缺失，跳过 {len(bitable_rows)} 条记录的写入", flush=True)
        for row in bitable_rows:
            bitable_fail_rows.append([
                row.get('title', ''),
                row.get('publish_date', ''),
                row.get('weekday', ''),
                row.get('url', ''),
                row.get('source', ''),
                'Pin' if row.get('is_pinned') else '',
                '多维表格不可用',
                row.get('message_index', ''),
                now_str
            ])

    # 提示跳过的记录
    if bitable_skipped_count > 0:
        print(f"⚠ {bitable_skipped_count} 条记录已存在于多维表格中，跳过写入", flush=True)

    # 更新已有记录的兴趣优先级（Pin 消息）
    if bitable_available and bitable_pin_updates:
        print(f"\n正在更新 {len(bitable_pin_updates)} 条 Pin 记录的兴趣优先级...", flush=True)
        update_ok = 0
        for item in bitable_pin_updates:
            priority_field = sort_cfg.get('priority_field', '兴趣优先级')
            if client.update_bitable_record(
                bitable_app_token, bitable_table_id,
                item['record_id'], {priority_field: '兴趣D'}
            ):
                update_ok += 1
            time.sleep(0.3)
        print(f"✓ 兴趣优先级更新完成: {update_ok}/{len(bitable_pin_updates)} 条", flush=True)

    # 写入多维表格失败日志
    if bitable_fail_rows:
        append_bitable_fail_log(bitable_fail_rows)
        print(f"✓ 多维表格失败日志: {len(bitable_fail_rows)} 条 -> {BITABLE_FAIL_LOG_FILE}", flush=True)

    # 撤回已处理的群消息
    # 撤回逻辑：只要写入总日志就撤回
    if csv_rows:
        # 收集所有已处理消息的 message_id（去重）
        recall_ids = list(set(
            item['message_id'] for item in url_messages
            if item['message_id']
        ))

        if recall_ids:
            recall_ids.reverse()
            print(f"\n正在撤回 {len(recall_ids)} 条已处理消息（倒序）...", flush=True)
            recall_ok = 0
            recall_fail = 0
            for mid in recall_ids:
                if client.recall_message(mid):
                    recall_ok += 1
                else:
                    recall_fail += 1
            print(f"✓ 撤回完成: 成功 {recall_ok} 条, 失败 {recall_fail} 条", flush=True)
        else:
            print("\n没有需要撤回的消息", flush=True)

    # 仅在入库成功时推进最后处理时间，避免写入失败后跳过未入库消息
    should_advance_state = (not bitable_rows) or bitable_write_ok
    if url_messages and should_advance_state:
        latest_time = max(item['message_time'] for item in url_messages)
        client.config['last_processed_time'] = latest_time
        set_config_value_preserve_comments(
            client.config_path,
            _config_path_for_profile(client, 'last_processed_time'),
            latest_time,
            comment=format_unix_ts_comment(latest_time / 1000),
        )
        latest_time_str = datetime.fromtimestamp(latest_time/1000).strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n✓ 已更新处理时间: {latest_time_str}", flush=True)
    elif url_messages:
        print("\n未更新 last_processed_time，原因：存在待写入多维表格记录但写入未完成", flush=True)

    # 输出统计
    print("\n" + "=" * 60, flush=True)
    print("处理完成", flush=True)
    print("=" * 60, flush=True)
    print(f"成功: {success_count} 条", flush=True)
    print(f"失败: {error_count} 条", flush=True)
    print(f"总计: {len(url_messages)} 条", flush=True)


if __name__ == "__main__":
    main()

