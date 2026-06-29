"""
知识星球文件下载脚本
将指定日期范围内帖子中的文件附件下载到本地，并修改文件时间戳为帖子发布时间。
支持多群组配置，文件名冲突时加 (2) 后缀。

用法:
  python src/dfZSXQ.py --his 20260420 20260422   # 历史下载
  python src/dfZSXQ.py --update                   # 增量更新

参数:
  --his START END    历史下载，指定起止日期（YYYYMMDD）
  --update           增量更新，从各群组 last_download_date 到今天

输出文件:
  log-err/zsxq_downloader_err.log    下载失败日志
"""

import os
import sys
import io
import re
import time
import ctypes
import argparse
import requests
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from feishu_client import FeishuClient
from modules.config_utils import (
    format_unix_ts_comment,
    set_list_item_scalar_preserve_comments,
)

ERROR_LOG = os.path.join(PROJECT_ROOT, 'log-err', 'zsxq_downloader_err.log')

ZSXQ_HEADERS_TPL = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://wx.zsxq.com/',
}

ZSXQ_SESSION = requests.Session()
ZSXQ_SESSION.trust_env = False


def update_group_last_download_marker(config_path, group_url, new_ts):
    """更新群组的 last_download_date，值为 Unix 时间戳，注释为可读时间。"""
    set_list_item_scalar_preserve_comments(
        config_path,
        section_key='zsxq_downloader',
        list_key='groups',
        match_key='group_url',
        match_value=group_url,
        target_key='last_download_date',
        value=int(new_ts),
        comment=format_unix_ts_comment(new_ts),
    )


def parse_last_download_marker(raw_value):
    """兼容旧 YYYYMMDD 和新 Unix 时间戳两种格式。"""
    text = str(raw_value or '').strip()
    if not text:
        return 0, ''
    if text.isdigit() and len(text) >= 10:
        return int(text), datetime.fromtimestamp(int(text)).strftime('%Y%m%d')
    if text.isdigit() and len(text) == 8:
        dt = datetime.strptime(text, '%Y%m%d')
        return int(dt.timestamp()), text
    return 0, ''


def _setup_encoding():
    if sys.platform == 'win32':
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


def zsxq_headers(token):
    h = dict(ZSXQ_HEADERS_TPL)
    h['Cookie'] = f'zsxq_access_token={token}'
    return h


def parse_zsxq_time(time_str):
    """解析知识星球时间字符串为 datetime（带时区）"""
    time_str = re.sub(r'(\+\d{2})(\d{2})$', r'\1:\2', time_str)
    try:
        return datetime.fromisoformat(time_str)
    except Exception:
        return None


def _api_failure_reason(data):
    message = data.get('msg') or data.get('message') or '接口返回 succeeded=false'
    code = data.get('code')
    return f'{message}（code={code}）' if code is not None else message


def _coerce_error_code(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _zsxq_failure_diagnosis(data=None, exc=None, status_code=None):
    """返回 (面向用户的原因说明, 是否建议重试)。"""
    if exc is not None:
        if isinstance(exc, requests.Timeout):
            return f'请求超时：{exc}', True
        if isinstance(exc, requests.ConnectionError):
            return f'网络连接失败：{exc}', True
        if isinstance(exc, ValueError):
            return f'响应解析失败：{exc}', True
        return f'请求异常：{exc}', True

    data = data or {}
    code = _coerce_error_code(data.get('code', status_code))
    raw_reason = _api_failure_reason(data)

    if code == 401:
        return (
            '知识星球认证失败：当前 zsxq.access_token 无效或已过期；'
            '请更新 ~/.config/secrets/gtokens.yaml 中的 zsxq.access_token',
            False,
        )
    if code == 403:
        return (
            '知识星球访问权限不足：请确认当前账号仍有星球成员权限，'
            '并确认 group_url/group_id 配置正确',
            False,
        )
    if code == 1059:
        return f'知识星球接口临时异常：{raw_reason}', True
    if code == 429:
        return f'知识星球接口限流：{raw_reason}', True
    if isinstance(code, int) and 500 <= code <= 599:
        return f'知识星球服务端异常：{raw_reason}', True
    if isinstance(status_code, int) and 500 <= status_code <= 599:
        return f'知识星球服务端异常：HTTP {status_code}', True
    if isinstance(status_code, int) and status_code >= 400:
        return f'知识星球接口返回 HTTP {status_code}：{raw_reason}', True
    return raw_reason, True


def fetch_topics_page(group_id, token, end_time=None, count=20):
    """获取帖子列表单页，含重试"""
    url = f'https://api.zsxq.com/v2/groups/{group_id}/topics?scope=all&count={count}'
    if end_time:
        url += f'&end_time={end_time.replace("+", "%2B")}'
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            resp = ZSXQ_SESSION.get(url, headers=zsxq_headers(token), timeout=15)
            data = resp.json()
            if data.get('succeeded'):
                if attempt > 1:
                    print(f'  获取帖子列表重试成功（尝试 {attempt}/{max_attempts}）')
                return data['resp_data']['topics']
            reason, retryable = _zsxq_failure_diagnosis(
                data=data, status_code=getattr(resp, 'status_code', None))
        except Exception as e:
            reason, retryable = _zsxq_failure_diagnosis(exc=e)

        if not retryable:
            print(f'  获取帖子列表失败（尝试 {attempt}/{max_attempts}）：'
                  f'{reason}；已停止重试')
            break
        if attempt < max_attempts:
            wait_seconds = 2 * attempt
            print(f'  获取帖子列表失败（尝试 {attempt}/{max_attempts}）：'
                  f'{reason}；{wait_seconds} 秒后重试')
            time.sleep(wait_seconds)
        else:
            print(f'  获取帖子列表失败（尝试 {attempt}/{max_attempts}）：'
                  f'{reason}；已停止重试')
    return None


def get_download_url(file_id, token):
    """获取文件临时下载链接，含重试"""
    url = f'https://api.zsxq.com/v2/files/{file_id}/download_url'
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            resp = ZSXQ_SESSION.get(url, headers=zsxq_headers(token), timeout=15)
            data = resp.json()
            if data.get('succeeded'):
                if attempt > 1:
                    print(f'  获取下载链接重试成功（尝试 {attempt}/{max_attempts}）')
                return data['resp_data']['download_url']
            reason, retryable = _zsxq_failure_diagnosis(
                data=data, status_code=getattr(resp, 'status_code', None))
        except Exception as e:
            reason, retryable = _zsxq_failure_diagnosis(exc=e)

        if not retryable:
            print(f'  获取下载链接失败（尝试 {attempt}/{max_attempts}）：'
                  f'{reason}；已停止重试')
            break
        if attempt < max_attempts:
            wait_seconds = 2 * attempt
            print(f'  获取下载链接失败（尝试 {attempt}/{max_attempts}）：'
                  f'{reason}；{wait_seconds} 秒后重试')
            time.sleep(wait_seconds)
        else:
            print(f'  获取下载链接失败（尝试 {attempt}/{max_attempts}）：'
                  f'{reason}；已停止重试')
    return None


def _set_windows_ctime(path, dt):
    """用 ctypes 调用 SetFileTime 修改 Windows 文件创建时间"""
    import ctypes.wintypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateFileW(path, 0x40000000, 0, None, 3, 0x02000000, None)
    if handle == ctypes.wintypes.HANDLE(-1).value:
        return
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    ft_val = int((dt.astimezone(timezone.utc) - epoch).total_seconds() * 10_000_000)

    class FILETIME(ctypes.Structure):
        _fields_ = [('dwLowDateTime', ctypes.wintypes.DWORD),
                    ('dwHighDateTime', ctypes.wintypes.DWORD)]

    ft = FILETIME(ft_val & 0xFFFFFFFF, ft_val >> 32)
    kernel32.SetFileTime(handle, ctypes.byref(ft), None, None)
    kernel32.CloseHandle(handle)


def set_file_timestamps(path, dt):
    ts = dt.timestamp()
    os.utime(path, (ts, ts))
    if sys.platform == 'win32':
        _set_windows_ctime(path, dt)


def resolve_dest_path(download_dir, name, topic_time):
    """确定下载目标路径。
    - 无同名文件：直接返回目标路径
    - 有同名文件且修改时间完全相同：依次尝试 (2)(3)… 后缀
    - 有同名文件但修改时间不同：返回 None（跳过下载）
    topic_time: datetime 对象，帖子发布时间
    """
    dest = os.path.join(download_dir, name)
    if not os.path.exists(dest):
        return dest

    existing_ts = int(os.path.getmtime(dest))
    topic_ts = int(topic_time.timestamp())
    if existing_ts != topic_ts:
        return None

    base, ext = os.path.splitext(name)
    n = 2
    while True:
        dest = os.path.join(download_dir, f'{base} ({n}){ext}')
        if not os.path.exists(dest):
            return dest
        n += 1


def download_file(download_url, dest_path):
    """流式下载文件，返回是否成功"""
    try:
        resp = ZSXQ_SESSION.get(download_url, stream=True, timeout=60)
        if resp.status_code != 200:
            return False
        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        return True
    except Exception as e:
        print(f'  下载异常: {e}')
        return False


def fetch_topics_in_range(group_id, token, start_date, end_date, start_ts_exclusive=0):
    """拉取范围内含文件的帖子，返回 (帖子列表, 是否完整抓取)。"""
    results = []
    end_time_param = None
    reached_before_start = False
    max_pages = 0 if start_date else 200  # 有起始日期则不限页数
    page = 0
    fetched_pages = 0
    completed = True
    seen_page_cursors = set()

    while max_pages == 0 or page < max_pages:
        page += 1
        topics = fetch_topics_page(group_id, token, end_time_param)
        if topics is None:
            completed = False
            print(f'  第 {page} 页最终获取失败，抓取中止'
                  f'（成功获取 {fetched_pages} 页）')
            break
        if not topics:
            break

        fetched_pages += 1
        last_ct = parse_zsxq_time(topics[-1].get('create_time', ''))
        last_date_str = last_ct.strftime('%Y-%m-%d') if last_ct else '?'

        for t in topics:
            ct_str = t.get('create_time', '')
            ct = parse_zsxq_time(ct_str)
            if ct is None:
                continue
            date_str = ct.strftime('%Y%m%d')
            topic_ts = int(ct.timestamp())

            if date_str > end_date:
                continue
            if start_ts_exclusive and topic_ts <= start_ts_exclusive:
                reached_before_start = True
                break
            if start_date and date_str < start_date:
                reached_before_start = True
                break

            files = t.get('talk', {}).get('files', [])
            if files:
                results.append({
                    'topic': t,
                    'date': date_str,
                    'create_time': ct,
                    'create_ts': topic_ts,
                })

        print(f'  翻页中... 第 {page} 页（{last_date_str}），'
              f'已找到 {len(results)} 个含文件帖子')
        if reached_before_start:
            break
        next_end_time = topics[-1].get('create_time')
        if not next_end_time:
            completed = False
            print(f'  第 {page} 页缺少分页游标，抓取中止')
            break
        if next_end_time == end_time_param or next_end_time in seen_page_cursors:
            completed = False
            print(f'  第 {page} 页分页游标未前进（{next_end_time}），'
                  '抓取中止，避免重复翻页')
            break
        seen_page_cursors.add(next_end_time)
        end_time_param = next_end_time
        time.sleep(0.5)
    else:
        completed = False
        print(f'  已达到 {max_pages} 页抓取上限，抓取中止')

    if completed:
        print(f'  翻页完成，共 {fetched_pages} 页')
    else:
        print(f'  抓取未完成：成功获取 {fetched_pages} 页，本次结果不会用于下载')
    results.sort(key=lambda x: x['create_time'])
    return results, completed


def process_topics(topic_items, download_dir, token):
    """下载帖子中的文件，文件名冲突时加 (2) 后缀，设置时间戳"""
    os.makedirs(download_dir, exist_ok=True)

    total_files = sum(len(item['topic']['talk']['files']) for item in topic_items)
    done = 0
    skipped = 0
    failed = 0
    max_done_ts = 0

    for item in topic_items:
        topic = item['topic']
        ct = item['create_time']
        files = topic['talk']['files']
        print(f'\n[{item["date"]}] {len(files)} 个文件  topic_id={topic["topic_id"]}')

        for f in files:
            file_id = str(f['file_id'])
            name = f['name']
            size = f.get('size', 0)
            dest = resolve_dest_path(download_dir, name, ct)
            if dest is None:
                print(f'  [跳过-已有] {name}')
                skipped += 1
                continue
            label = os.path.basename(dest)

            print(f'  [下载] {label} ({size/1024/1024:.1f} MB)', end='', flush=True)
            dl_url = get_download_url(file_id, token)
            if not dl_url:
                print(' → 获取下载链接失败')
                failed += 1
                with open(ERROR_LOG, 'a', encoding='utf-8') as ef:
                    ef.write(f'{datetime.now()} [{item["date"]}] file_id={file_id} name={name} 获取下载链接失败\n')
                continue

            ok = download_file(dl_url, dest)
            if ok:
                set_file_timestamps(dest, ct)
                done += 1
                max_done_ts = max(max_done_ts, int(ct.timestamp()))
                print(' ✓')
            else:
                failed += 1
                print(' ✗')
                with open(ERROR_LOG, 'a', encoding='utf-8') as ef:
                    ef.write(f'{datetime.now()} [{item["date"]}] file_id={file_id} name={name} 下载失败\n')

            time.sleep(0.5)

    print(f'\n完成: 下载 {done}，跳过 {skipped}，失败 {failed}，共 {total_files} 个文件')
    return done, skipped, failed, max_done_ts


def process_group(group_cfg, token, start_date, end_date, config_path):
    """处理单个群组的下载"""
    name = group_cfg.get('name', '')
    group_url = group_cfg.get('group_url', '')
    download_dir = group_cfg.get('download_dir', '')
    last_marker = group_cfg.get('last_download_date', '')
    last_ts, last_date = parse_last_download_marker(last_marker)

    m = re.search(r'/group/(\d+)', group_url)
    if not m:
        print(f'  无法从 group_url 提取 group_id: {group_url}')
        return
    group_id = m.group(1)

    print(f'\n{"=" * 60}')
    print(f'群组: {name}  ({group_id})')
    print(f'下载范围: {start_date} ~ {end_date}')
    print(f'目标目录: {download_dir}')

    topic_items, fetch_completed = fetch_topics_in_range(
        group_id, token, start_date, end_date, start_ts_exclusive=last_ts)
    if not fetch_completed:
        print('本群组处理失败：帖子列表未完整获取，本次结果作废')
        print('last_download_date 未更新，原因：帖子列表抓取失败')
        return False

    print(f'找到 {len(topic_items)} 个含文件的帖子')

    if not topic_items:
        print('无文件需要下载')
        print('last_download_date 未更新，原因：本次没有发现可下载文件')
        return True

    done, skipped, failed, max_done_ts = process_topics(topic_items, download_dir, token)

    if done > 0 and failed == 0:
        update_group_last_download_marker(config_path, group_url, max_done_ts)
        group_cfg['last_download_date'] = max_done_ts
        print(f'last_download_date 已更新: {last_marker} → {max_done_ts} '
              f'({format_unix_ts_comment(max_done_ts)})')
    elif done == 0 and failed == 0:
        print(f'last_download_date 未更新，原因：本次没有实际下载新文件（跳过 {skipped} 个）')
    else:
        print(f'last_download_date 未更新，原因：仍有 {failed} 个文件下载失败')
    return failed == 0


def main():
    _setup_encoding()

    ap = argparse.ArgumentParser(description='知识星球文件下载')
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument('--his', nargs=2, metavar=('START', 'END'),
                     help='历史下载，指定起止日期 YYYYMMDD YYYYMMDD')
    grp.add_argument('--update', action='store_true',
                     help='增量更新，从各群组 last_download_date 次日到今天')
    args = ap.parse_args()

    client = FeishuClient()
    token = client.credentials.get('zsxq', {}).get('access_token', '')
    if not token:
        print('未配置 zsxq.access_token，请检查 ~/.config/secrets/gtokens.yaml')
        return

    groups = client.config.get('zsxq_downloader', {}).get('groups', [])
    if not groups:
        print('未配置 zsxq_downloader.groups，请检查 config.yaml')
        return

    config_path = os.path.join(PROJECT_ROOT, 'cfg', 'config.yaml')
    today = datetime.now().strftime('%Y%m%d')

    for group_cfg in groups:
        last_marker = group_cfg.get('last_download_date', '')
        last_ts, last_date = parse_last_download_marker(last_marker)

        if args.his:
            start_date, end_date = args.his
        else:
            if not last_marker:
                print(f'群组 {group_cfg.get("name")} 未配置 last_download_date，跳过')
                continue
            if last_date and last_date > today:
                print(f'\n群组 {group_cfg.get("name")}: 已是最新（last={last_date}），跳过')
                continue
            start_date = last_date
            end_date = today

        process_group(group_cfg, token, start_date, end_date, config_path)


if __name__ == '__main__':
    main()
