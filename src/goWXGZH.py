"""
微信公众号历史文章 → 飞书多维表格
基于公众号清单，抓取指定日期范围的文章，解析后写入飞书多维表格。

用法:
  python src/goWXGZH.py --his 20260322 20260331   # 历史批量处理
  python src/goWXGZH.py --update                   # 增量更新
  python src/goWXGZH.py --searchbiz "名称"          # 搜索公众号 fakeid
  python src/goWXGZH.py --update --list <file>     # 指定公众号清单文件
  python src/goWXGZH.py --update --refresh-cache   # 刷新多维表格缓存

参数:
  --his START END    历史批量处理，指定日期范围（YYYYMMDD）
  --update           增量更新（基于各公众号 last_update 时间戳）
  --searchbiz KEYWORD  搜索公众号 fakeid
  --list FILE        指定公众号清单文件（默认 cfg/wxgzh_list.yaml）
  --refresh-cache    强制刷新多维表格 URL 缓存

输出文件:
  log-err/wxgzh_error_log.csv        解析失败日志
  data/bitable_cache_*.csv           多维表格 URL 缓存
"""

import csv
import io
import json
import os
import re
import sys
import time
import argparse
import random
import base64
import ctypes
import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import requests
import yaml


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
from url_parser import UrlParser
from modules.config_utils import update_config_field
from modules.bitable_url_cache import BitableUrlCache

LOG_ERR_DIR = os.path.join(PROJECT_ROOT, 'log-err')
os.makedirs(LOG_ERR_DIR, exist_ok=True)

DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

ERROR_LOG_FILE = os.path.join(LOG_ERR_DIR, 'wxgzh_error_log.csv')
ERROR_HEADERS = ['公众号', '链接', '标题', '错误类型', '错误信息', '记录时间']

WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

DEFAULT_LIST_FILE = os.path.join(PROJECT_ROOT, 'cfg', 'wxgzh_list.yaml')
LAST_UPDATE_STATE_FILE = os.path.join(DATA_DIR, 'wxgzh_last_update_state.json')

# 微信后台 API 常量
WX_APPMSG_URL = 'https://mp.weixin.qq.com/cgi-bin/appmsg'
WX_PUBLISH_URL = 'https://mp.weixin.qq.com/cgi-bin/appmsgpublish'
WX_MP_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept': '*/*',
    'Referer': 'https://mp.weixin.qq.com/',
}
WX_SESSION = requests.Session()
WX_SESSION.trust_env = False

if sys.platform == 'darwin':
    CHROME_USER_DATA_DIRS = [
        Path.home() / 'Library/Application Support/Google/Chrome',
        Path.home() / 'Library/Application Support/Chromium',
        Path.home() / 'Library/Application Support/Google/Chrome for Testing',
        Path.home() / 'Library/Application Support/Google/ChromeForTesting',
    ]
elif sys.platform == 'win32':
    CHROME_USER_DATA_DIRS = [
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/User Data',
    ]
else:
    CHROME_USER_DATA_DIRS = [
        Path.home() / '.config/google-chrome',
        Path.home() / '.config/chromium',
    ]


def _chrome_cookie_db_candidates():
    candidates = []
    for user_data in CHROME_USER_DATA_DIRS:
        profile_dirs = [user_data / 'Default']
        if user_data.exists():
            profile_dirs.extend(sorted(user_data.glob('Profile *')))
        for profile_dir in profile_dirs:
            candidates.append(profile_dir / 'Network/Cookies')
            candidates.append(profile_dir / 'Cookies')
    return candidates


CHROME_COOKIE_DBS = _chrome_cookie_db_candidates()
CHROME_COOKIE_DB = str(next((p for p in CHROME_COOKIE_DBS if p.exists()), CHROME_COOKIE_DBS[0]))
CHROME_LOCAL_STATE = str(next(
    (p / 'Local State' for p in CHROME_USER_DATA_DIRS if (p / 'Local State').exists()),
    CHROME_USER_DATA_DIRS[0] / 'Local State',
))
SECRETS_DIR = Path.home() / '.config' / 'secrets'
GTOKENS_PATH = SECRETS_DIR / 'gtokens.yaml'


def _deep_merge(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _load_secrets_credentials():
    credentials = {}
    for name in ('global', 'gkeys', 'gfeishu', 'gtokens'):
        path = SECRETS_DIR / f'{name}.yaml'
        if not path.exists():
            continue
        with open(path, 'r', encoding='utf-8') as f:
            _deep_merge(credentials, yaml.safe_load(f) or {})
    return credentials


def _save_wechat_cookie(cookie):
    data = {}
    if GTOKENS_PATH.exists():
        with open(GTOKENS_PATH, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    data.setdefault('wechat', {})['cookie'] = cookie
    with open(GTOKENS_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _get_mp_token(cookie):
    """访问微信公众号后台首页，从重定向 URL 中提取 token

    Returns:
        str | None: token 字符串，失败返回 None
    """
    headers = dict(WX_MP_HEADERS)
    headers['Cookie'] = cookie
    headers['Accept'] = ('text/html,application/xhtml+xml,'
                         'application/xml;q=0.9,*/*;q=0.8')
    try:
        resp = WX_SESSION.get('https://mp.weixin.qq.com/',
                              headers=headers, allow_redirects=False,
                              timeout=15)
        location = resp.headers.get('Location', '')
        m = re.search(r'token=(\d+)', location)
        if m:
            return m.group(1)
        print('  无法从后台首页获取 token，cookie 可能已过期')
        print(f'  Status={resp.status_code}, Location={location[:100]}')
        return None
    except requests.RequestException as e:
        print(f'  获取 token 失败: {e}')
        return None


def _dpapi_decrypt(data: bytes) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.c_uint),
                    ('pbData', ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out)):
        raise RuntimeError('CryptUnprotectData failed')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _load_chrome_master_key():
    if sys.platform == 'darwin':
        try:
            password = subprocess.check_output(
                ['security', 'find-generic-password', '-w',
                 '-s', 'Chrome Safe Storage'],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            return None
        return hashlib.pbkdf2_hmac(
            'sha1', password.encode('utf-8'), b'saltysalt', 1003, 16)

    if not os.path.exists(CHROME_LOCAL_STATE):
        return None
    with open(CHROME_LOCAL_STATE, 'r', encoding='utf-8') as f:
        local_state = json.load(f)
    enc_key_b64 = local_state.get('os_crypt', {}).get('encrypted_key', '')
    if not enc_key_b64:
        return None
    enc_key = base64.b64decode(enc_key_b64)
    if enc_key.startswith(b'DPAPI'):
        enc_key = enc_key[5:]
    return _dpapi_decrypt(enc_key)


def _decrypt_chrome_cookie(encrypted_value: bytes, master_key: bytes) -> str:
    if not encrypted_value:
        return ''
    if sys.platform == 'darwin' and encrypted_value.startswith(b'v10'):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        decryptor = Cipher(
            algorithms.AES(master_key), modes.CBC(b' ' * 16)
        ).decryptor()
        decrypted = decryptor.update(encrypted_value[3:]) + decryptor.finalize()
        pad_len = decrypted[-1]
        if 0 < pad_len <= 16:
            decrypted = decrypted[:-pad_len]
        return decrypted.decode('utf-8')

    if encrypted_value.startswith(b'v10') or encrypted_value.startswith(b'v11'):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        return AESGCM(master_key).decrypt(nonce, ciphertext, None).decode('utf-8')
    return _dpapi_decrypt(encrypted_value).decode('utf-8')


def _load_wechat_cookie_from_chrome():
    """从本机 Chrome 登录态提取 mp.weixin.qq.com cookie。"""
    if not os.path.exists(CHROME_COOKIE_DB):
        return ''
    master_key = _load_chrome_master_key()
    if not master_key:
        return ''

    tmp_fd, tmp_path = tempfile.mkstemp(prefix='wx_cookie_', suffix='.db')
    os.close(tmp_fd)
    try:
        shutil.copy2(CHROME_COOKIE_DB, tmp_path)
        conn = sqlite3.connect(tmp_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT name, encrypted_value, value
            FROM cookies
            WHERE host_key IN ('mp.weixin.qq.com', '.mp.weixin.qq.com')
        """)
        parts = []
        for name, encrypted_value, plain_value in cur.fetchall():
            value = plain_value or ''
            if not value:
                try:
                    value = _decrypt_chrome_cookie(encrypted_value, master_key)
                except Exception:
                    value = ''
            if value:
                parts.append(f'{name}={value}')
        conn.close()
        return '; '.join(parts)
    except Exception as e:
        print(f'  从 Chrome 读取 cookie 失败: {type(e).__name__}: {e}')
        return ''
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _load_wechat_cookie_from_chrome_robust():
    """More robust Chrome cookie loader that tolerates a live browser lock."""
    cookie_dbs = [p for p in CHROME_COOKIE_DBS if p.exists()]
    if not cookie_dbs:
        return ''
    master_key = _load_chrome_master_key()
    if not master_key:
        return ''

    def _read_cookie_rows(conn):
        cur = conn.cursor()
        cur.execute("""
            SELECT name, encrypted_value, value
            FROM cookies
            WHERE host_key IN ('mp.weixin.qq.com', '.mp.weixin.qq.com')
        """)
        return cur.fetchall()

    def _rows_to_cookie(rows):
        parts = []
        for name, encrypted_value, plain_value in rows:
            value = plain_value or ''
            if not value:
                try:
                    value = _decrypt_chrome_cookie(encrypted_value, master_key)
                except Exception:
                    value = ''
            if value:
                parts.append(f'{name}={value}')
        return '; '.join(parts)

    for cookie_db in cookie_dbs:
        cookie_db_str = str(cookie_db)
        try:
            db_uri = cookie_db.resolve().as_uri() + '?mode=ro'
            conn = sqlite3.connect(db_uri, uri=True)
            try:
                cookie = _rows_to_cookie(_read_cookie_rows(conn))
                if cookie:
                    return cookie
            finally:
                conn.close()
        except Exception:
            pass

        tmp_fd, tmp_path = tempfile.mkstemp(prefix='wx_cookie_', suffix='.db')
        os.close(tmp_fd)
        try:
            shutil.copy2(cookie_db_str, tmp_path)
            wal_path = cookie_db_str + '-wal'
            shm_path = cookie_db_str + '-shm'
            if os.path.exists(wal_path):
                try:
                    shutil.copy2(wal_path, tmp_path + '-wal')
                except OSError:
                    pass
            if os.path.exists(shm_path):
                try:
                    shutil.copy2(shm_path, tmp_path + '-shm')
                except OSError:
                    pass
            conn = sqlite3.connect(tmp_path)
            try:
                cookie = _rows_to_cookie(_read_cookie_rows(conn))
                if cookie:
                    return cookie
            finally:
                conn.close()
        except Exception as e:
            print(f'  从 Chrome 读取 cookie 失败: {type(e).__name__}: {e}')
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            for suffix in ('-wal', '-shm'):
                try:
                    os.remove(tmp_path + suffix)
                except OSError:
                    pass
    return ''


def _refresh_wechat_cookie_from_chrome(client):
    """尝试从 Chrome 自动刷新微信公众号后台 cookie。"""
    print('  尝试从 Chrome 登录态自动获取微信公众号 cookie...', flush=True)
    cookie = _load_wechat_cookie_from_chrome_robust()
    if not cookie:
        print('  Chrome 中未找到可用的 mp.weixin.qq.com cookie', flush=True)
        return ''
    token = _get_mp_token(cookie)
    if not token:
        print('  Chrome 登录态中的 cookie 也无法获取后台 token', flush=True)
        return ''
    client.credentials.setdefault('wechat', {})['cookie'] = cookie
    _save_wechat_cookie(cookie)
    print('  已从 Chrome 自动刷新 cookie，并写回 ~/.config/secrets/gtokens.yaml',
          flush=True)
    return cookie


def search_biz(query, cookie, token, count=5):
    """通过微信后台 searchbiz API 搜索公众号

    Args:
        query: 搜索关键词（公众号名称）
        cookie: 微信后台 cookie
        token: 后台 token
        count: 返回结果数

    Returns:
        list of dict: [{nickname, alias, fakeid}, ...]
    """
    params = {
        'action': 'search_biz',
        'begin': 0,
        'count': count,
        'query': query,
        'token': token,
        'lang': 'zh_CN',
        'f': 'json',
        'ajax': 1,
    }
    headers = dict(WX_MP_HEADERS)
    headers['Cookie'] = cookie
    try:
        resp = WX_SESSION.get(
            'https://mp.weixin.qq.com/cgi-bin/searchbiz',
            params=params, headers=headers, timeout=15)
        data = resp.json()
        results = []
        for item in data.get('list', []):
            nickname = item.get('nickname', '')
            try:
                nickname = nickname.encode('latin1').decode('utf-8')
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
            results.append({
                'nickname': nickname,
                'alias': item.get('alias', ''),
                'fakeid': item.get('fakeid', ''),
            })
        return results
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f'搜索失败: {e}')
        return []


def append_error_log(rows):
    """追加错误记录到日志文件"""
    if not rows:
        return
    need_header = (not os.path.exists(ERROR_LOG_FILE) or
                   os.path.getsize(ERROR_LOG_FILE) == 0)
    with open(ERROR_LOG_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(ERROR_HEADERS)
        writer.writerows(rows)


def load_account_list(list_file):
    """加载公众号清单"""
    with open(list_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    accounts = data.get('accounts', [])
    # 过滤掉 biz 为空的条目
    return [a for a in accounts if a.get('biz')]


def save_account_list(list_file, accounts_data):
    """保存公众号清单（保留注释用文本替换）"""
    with open(list_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for acct in accounts_data:
        name = acct['name']
        old_ts = acct.get('_old_last_update')
        new_ts = acct.get('last_update')
        if old_ts is not None and new_ts is not None and old_ts != new_ts:
            date_comment = datetime.fromtimestamp(
                new_ts).strftime('%Y%m%d-%H:%M')
            # 先找到该公众号 name 所在行，再往下找最近的 last_update 行
            found_name = False
            for i, line in enumerate(lines):
                if not found_name:
                    if re.search(rf'name:\s*{re.escape(name)}\s*$', line):
                        found_name = True
                    continue
                # 找到 name 后，匹配下方最近的 last_update 行
                m = re.match(
                    r'^(\s+last_update:\s*)\S+(    #.*)?$', line)
                if m:
                    lines[i] = (f'{m.group(1)}{new_ts}'
                                f'    # {date_comment}\n')
                    break
                # 遇到下一个 name 说明该账号没有 last_update，停止
                if 'name:' in line:
                    break
    with open(list_file, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def load_last_update_state():
    """Load per-account successful write watermark state."""
    if not os.path.exists(LAST_UPDATE_STATE_FILE):
        return {}
    try:
        with open(LAST_UPDATE_STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_last_update_state(state):
    """Persist per-account successful write watermark state."""
    with open(LAST_UPDATE_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def record_successful_account_update(state, acct, last_success_ts):
    """Update local success watermark for one public account."""
    biz = acct.get('biz', '')
    if not biz or not last_success_ts:
        return
    state[biz] = {
        'name': acct.get('name', ''),
        'last_success_ts': int(last_success_ts),
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def build_bitable_last_update_map(client, wxgzh_config):
    """Build conservative per-account last_update candidates from bitable records."""
    bt_cfg = wxgzh_config.get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    if not app_token or not table_id:
        return {}

    records = client.get_bitable_records(app_token, table_id, ['来源', '日期'])
    source_to_ts = {}
    for rec in records:
        source = str(rec.get('来源', '') or '').strip()
        if not source.startswith('微信-'):
            continue
        date_val = rec.get('日期', '')
        if isinstance(date_val, (int, float)):
            date_str = str(int(date_val))
        else:
            date_str = str(date_val).strip()
        if not (len(date_str) == 8 and date_str.isdigit()):
            continue
        try:
            ts = date_to_timestamp(date_str, end_of_day=False) + 1
        except ValueError:
            continue
        if ts > source_to_ts.get(source, 0):
            source_to_ts[source] = ts
    return source_to_ts


def repair_last_update_from_state(accounts, state, source_to_ts=None):
    """Repair account last_update using local success state, then bitable fallback."""
    repaired = []
    source_to_ts = source_to_ts or {}

    for acct in accounts:
        biz = acct.get('biz', '')
        name = acct.get('name', '')
        current_ts = int(acct.get('last_update', 0) or 0)
        candidate_ts = 0
        reason = ''

        st = state.get(biz, {})
        state_ts = int(st.get('last_success_ts', 0) or 0)
        if state_ts:
            candidate_ts = state_ts
            reason = 'local_state'
        else:
            bitable_ts = int(source_to_ts.get(f'微信-{name}', 0) or 0)
            if bitable_ts:
                candidate_ts = bitable_ts
                reason = 'bitable_date'

        if candidate_ts and candidate_ts != current_ts:
            acct['last_update'] = candidate_ts
            repaired.append({
                'name': name,
                'biz': biz,
                'old_ts': current_ts,
                'new_ts': candidate_ts,
                'reason': reason,
            })

    return repaired


def date_to_timestamp(date_str, end_of_day=False):
    """YYYYMMDD → Unix 时间戳（秒）"""
    dt = datetime.strptime(date_str, '%Y%m%d')
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def calc_weekday(date_str):
    """根据 YYYYMMDD 日期字符串计算星期"""
    try:
        dt = datetime.strptime(date_str, '%Y%m%d')
        return WEEKDAY_NAMES[dt.weekday()]
    except (ValueError, TypeError):
        return ''


# freq 字母 → 对应小时
FREQ_HOURS = {'A': 1, 'B': 7, 'C': 13, 'D': 19}


def get_update_cutoff(freq_str, now=None):
    """根据 freq 配置和当前时间，计算本次 --update 的截止时间戳

    策略：找到当前时间之前、最近一个已到达的 freq 时间点作为截止。
    如果当前时间还没到达今天任何一个 freq 时间点，则回退到昨天最后一个。

    Args:
        freq_str: 如 'A、B、C、D' 或 'A' 或 'C、D'
        now: 当前时间（测试用），默认 datetime.now()

    Returns:
        int | None: 截止时间戳（秒），None 表示当前无需检查
    """
    if now is None:
        now = datetime.now()

    # 解析 freq 字母列表
    letters = [s.strip().upper() for s in freq_str.replace('、', ',').split(',')]
    hours = sorted(FREQ_HOURS[l] for l in letters if l in FREQ_HOURS)
    if not hours:
        return None

    # 找今天已到达的最晚时间点
    today = now.replace(minute=0, second=0, microsecond=0)
    for h in reversed(hours):
        cutoff = today.replace(hour=h)
        if now >= cutoff:
            return int(cutoff.timestamp())

    # 今天还没到任何时间点，回退到昨天最后一个
    yesterday = (now - timedelta(days=1)).replace(
        hour=hours[-1], minute=0, second=0, microsecond=0)
    return int(yesterday.timestamp())


def _wx_api_request(fakeid, cookie, token, begin=0, count=5):
    """调用微信公众号后台 appmsg API（单次请求，含重试）

    Args:
        fakeid: 公众号 fakeid（即 biz 参数）
        cookie: 微信后台 cookie
        token: 后台 token（从首页重定向获取）
        begin: 分页起始位置
        count: 每页条数

    Returns:
        dict | None: 成功返回 JSON，失败返回 None
    """
    params = {
        'action': 'list_ex',
        'begin': begin,
        'count': count,
        'fakeid': fakeid,
        'type': 9,
        'query': '',
        'token': token,
        'lang': 'zh_CN',
        'f': 'json',
        'ajax': 1,
    }
    headers = dict(WX_MP_HEADERS)
    headers['Cookie'] = cookie

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = WX_SESSION.get(WX_APPMSG_URL, params=params,
                                  headers=headers, timeout=15)
            data = resp.json()
            ret = data.get('base_resp', {}).get('ret', -1)
            if ret != 0:
                errmsg = data.get('base_resp', {}).get('err_msg', '')
                print(f'    后台 API 错误: ret={ret}, errmsg={errmsg}')
                # ret=200013 表示频率限制，等待后重试
                if ret == 200013 and attempt < max_retries - 1:
                    wait = 30 * (2 ** attempt)  # 30, 60, 120
                    print(f'    频率限制，{wait}s 后重试 '
                          f'[{attempt+1}/{max_retries}]')
                    time.sleep(wait)
                    continue
                return None
            return data
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt < max_retries - 1:
                wait = 5 * (2 ** attempt)  # 5, 10, 20
                print(f'    请求失败({e})，{wait}s 后重试 '
                      f'[{attempt+1}/{max_retries}]')
                time.sleep(wait)
            else:
                print(f'    请求失败，已重试 {max_retries} 次: {e}')
                return None


def _extract_article_from_item(item):
    """从后台 appmsg API 返回的单条记录中提取文章信息

    Returns:
        dict | None: {url, title, publish_ts}，无效则返回 None
    """
    # 跳过审核中/草稿状态的记录（checking=1）
    if item.get('checking', 0):
        return None
    link = (item.get('link') or '').strip()
    if not link:
        return None
    # http → https
    if link.startswith('http://'):
        link = 'https://' + link[7:]
    # 去掉 #rd fragment
    if '#rd' in link:
        link = link.split('#rd')[0]
    title = (item.get('title') or '').strip()
    # 优先用 update_time（审核通过/上线时间），回退到 create_time（提交时间）
    publish_ts = item.get('update_time') or item.get('create_time', 0)
    return {'url': link, 'title': title, 'publish_ts': publish_ts}


def _wx_publish_request(fakeid, cookie, token, begin=0, count=5):
    """调用微信公众号后台 appmsgpublish API（获取"发布"类型文章）

    Returns:
        dict | None: 成功返回 JSON，失败返回 None
    """
    params = {
        'sub': 'list',
        'begin': begin,
        'count': count,
        'fakeid': fakeid,
        'token': token,
        'lang': 'zh_CN',
        'f': 'json',
        'ajax': 1,
    }
    headers = dict(WX_MP_HEADERS)
    headers['Cookie'] = cookie

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = WX_SESSION.get(WX_PUBLISH_URL, params=params,
                                  headers=headers, timeout=15)
            data = resp.json()
            ret = data.get('base_resp', {}).get('ret', -1)
            if ret != 0:
                errmsg = data.get('base_resp', {}).get('err_msg', '')
                print(f'    发布 API 错误: ret={ret}, errmsg={errmsg}')
                if ret == 200013 and attempt < max_retries - 1:
                    wait = 30 * (2 ** attempt)
                    print(f'    频率限制，{wait}s 后重试 '
                          f'[{attempt+1}/{max_retries}]')
                    time.sleep(wait)
                    continue
                return None
            return data
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt < max_retries - 1:
                wait = 5 * (2 ** attempt)
                print(f'    请求失败({e})，{wait}s 后重试 '
                      f'[{attempt+1}/{max_retries}]')
                time.sleep(wait)
            else:
                print(f'    请求失败，已重试 {max_retries} 次: {e}')
                return None


def _extract_publish_articles(publish_info, ts_start, ts_end):
    """从 appmsgpublish 的单条发布记录中提取文章

    处理两种类型：
    - type=9（群发）：时间从 sent_info.time 获取
    - type=10002（发布）：时间从 publish_info.update_time 获取

    Returns:
        list of dict: [{url, title, publish_ts}, ...]
    """
    if isinstance(publish_info, str):
        publish_info = json.loads(publish_info)

    pub_type = publish_info.get('type', 0)

    # 获取发布时间：群发用 sent_info.time，发布用 publish_info.update_time
    pub_ts = 0
    if pub_type == 9:
        pub_ts = publish_info.get('sent_info', {}).get('time', 0)
    elif pub_type == 10002:
        inner_info = publish_info.get('publish_info', {})
        pub_ts = (inner_info.get('update_time')
                  or inner_info.get('create_time', 0))
    else:
        return []

    if not pub_ts:
        return []

    # 时间范围过滤
    if pub_ts < ts_start or pub_ts > ts_end:
        return []

    articles = []
    for item in publish_info.get('appmsg_info', []):
        link = (item.get('content_url') or '').strip()
        if not link:
            continue
        if link.startswith('http://'):
            link = 'https://' + link[7:]
        if '#rd' in link:
            link = link.split('#rd')[0]
        title = (item.get('title') or '').strip()
        articles.append({
            'url': link, 'title': title, 'publish_ts': pub_ts,
        })
    return articles


def fetch_articles(biz, cookie, token, ts_start, ts_end, account_name=''):
    """分页抓取公众号文章列表，按时间戳范围过滤

    使用 appmsgpublish API，同时包含群发(type=9)和发布(type=10002)文章，
    且不会返回废弃草稿。

    Args:
        biz: 公众号 fakeid（__biz 参数）
        cookie: 微信后台 cookie
        token: 后台 token
        ts_start: 起始时间戳（含）
        ts_end: 结束时间戳（含）
        account_name: 公众号名称（日志用）

    Returns:
        (articles, error_rows)
        articles: [{url, title, publish_ts}, ...]
        error_rows: 漏抓错误日志行
    """
    articles = []
    error_rows = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    begin = 0
    count = 20  # 每页 20 次发布，减少翻页次数降低频率限制风险
    page = 0

    while True:
        page += 1
        data = _wx_publish_request(biz, cookie, token,
                                   begin=begin, count=count)
        if data is None:
            error_rows.append([
                account_name, '', '', '抓取失败',
                f'第 {page} 页 begin={begin} 请求失败', now_str])
            break

        pp = data.get('publish_page', {})
        if isinstance(pp, str):
            pp = json.loads(pp)

        plist = pp.get('publish_list', [])
        if not plist:
            break

        if page == 1:
            pub_cnt = pp.get('publish_count', 0)
            mass_cnt = pp.get('masssend_count', 0)
            print(f'    共 {pub_cnt + mass_cnt} 篇'
                  f'（发布 {pub_cnt} + 群发 {mass_cnt}）')

        page_earliest_ts = None
        page_collected = 0
        for pub in plist:
            pi = pub.get('publish_info', {})
            if isinstance(pi, str):
                pi = json.loads(pi)

            arts = _extract_publish_articles(pi, ts_start, ts_end)
            if arts:
                articles.extend(arts)
                page_collected += len(arts)

            # 跟踪最早时间（用于翻页终止）
            pub_type = pi.get('type', 0)
            pub_ts = 0
            if pub_type == 9:
                pub_ts = pi.get('sent_info', {}).get('time', 0)
            else:
                inner = pi.get('publish_info', {})
                pub_ts = inner.get('create_time', 0)
            if pub_ts:
                if page_earliest_ts is None or pub_ts < page_earliest_ts:
                    page_earliest_ts = pub_ts

        if page_collected:
            print(f'    第 {page} 页命中 {page_collected} 篇')

        if page_earliest_ts is not None and page_earliest_ts < ts_start:
            break
        if len(plist) < count:
            break

        begin += count
        time.sleep(random.uniform(5, 10))

    # URL 去重（同一篇文章不会重复，但以防万一）
    seen = set()
    unique = []
    for art in articles:
        if art['url'] not in seen:
            seen.add(art['url'])
            unique.append(art)
    articles = unique

    return articles, error_rows


def parse_articles(url_parser, articles, account_name=''):
    """用 UrlParser 解析每篇文章的详细信息

    Args:
        url_parser: UrlParser 实例
        articles: fetch_articles 返回的文章列表
        account_name: 公众号名称（日志用）

    Returns:
        (parsed_rows, error_rows)
        parsed_rows: [{链接, 标题, 来源, 日期, 星期}, ...]
        error_rows: 解析失败的错误日志行
    """
    parsed_rows = []
    error_rows = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total = len(articles)

    for idx, art in enumerate(articles, 1):
        url = art['url']
        print(f'  [{idx}/{total}] {url[:80]}', flush=True)

        # 解析，含重试
        info = None
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                info = url_parser.parse_url(url)
                if not info.get('error_info'):
                    break
            except Exception as e:
                info = {'error_info': str(e), 'title': '', 'source': '',
                        'publish_date': '', 'weekday': '', 'url': url}
            if attempt < max_retries:
                time.sleep(3)
                print(f'    重试 [{attempt+1}/{max_retries}]', flush=True)

        title = info.get('title', '') or art.get('title', '')
        source = info.get('source', '')
        pub_date = info.get('publish_date', '')
        weekday = info.get('weekday', '')
        parsed_url = info.get('url', url)

        # 如果 url_parser 没拿到日期，用微信 API 返回的时间戳
        if not pub_date and art.get('publish_ts'):
            dt = datetime.fromtimestamp(art['publish_ts'])
            pub_date = dt.strftime('%Y%m%d')
            weekday = calc_weekday(pub_date)

        print(f'    标题: {title[:50] if title else "(无)"}  '
              f'来源: {source or "(无)"}  日期: {pub_date or "(无)"}',
              flush=True)

        if info.get('error_info'):
            error_rows.append([
                account_name, parsed_url, title, '解析异常',
                info['error_info'], now_str])

        parsed_rows.append({
            '链接': parsed_url,
            '标题': title,
            '来源': source,
            '日期': pub_date,
            '星期': weekday,
        })

    return parsed_rows, error_rows


def write_to_bitable(client, parsed_rows, wxgzh_config, existing_urls):
    """去重后写入飞书多维表格

    Args:
        client: FeishuClient 实例
        parsed_rows: parse_articles 返回的解析结果
        wxgzh_config: config.yaml 中 wxgzh 配置
        existing_urls: 多维表格中已有的 URL 集合

    Returns:
        (int, list): (成功写入数, [{'url':..., 'record_id':...}, ...])
    """
    bt_cfg = wxgzh_config.get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    if not app_token or not table_id:
        print('  多维表格配置缺失，跳过写入')
        return 0, [], False

    new_records = []
    new_urls = []   # 与 new_records 保持同序，用于写入后关联 record_id
    skipped = 0
    for row in parsed_rows:
        url = row['链接']
        if url in existing_urls:
            skipped += 1
            continue

        fields = {
            '链接': url,
            '标题': row['标题'],
            '来源': row['来源'],
        }
        if row['日期']:
            fields['日期'] = (int(row['日期'])
                             if str(row['日期']).isdigit()
                             else row['日期'])
        if row['星期']:
            fields['星期'] = row['星期']

        new_records.append(fields)
        new_urls.append(url)

    if skipped:
        print(f'  跳过已存在: {skipped} 条')

    if not new_records:
        print('  无新记录需要写入')
        return 0, [], True

    print(f'  准备写入 {len(new_records)} 条新记录...')

    # 飞书 API 断连重试
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = client.batch_add_bitable_records(
                app_token, table_id, new_records)
            success = result.get('success', 0)
            failed = result.get('failed', 0)
            print(f'  写入完成: 成功 {success}, 失败 {failed}')
            # 构建缓存追加条目：将 record_id 与 url 对应
            created = result.get('records', [])
            cache_entries = []
            for i, rec in enumerate(created):
                url = new_urls[i] if i < len(new_urls) else ''
                rid = rec.get('record_id', '') if isinstance(rec, dict) else ''
                if url:
                    cache_entries.append({'url': url, 'record_id': rid})
                    existing_urls.add(url)
            write_ok = (failed == 0 and len(created) == len(new_records))
            return success, cache_entries, write_ok
        except (ConnectionError, ConnectionResetError) as e:
            if attempt < max_retries - 1:
                print(f'  飞书 API 断连({e})，5s 后重试 '
                      f'[{attempt+1}/{max_retries}]')
                time.sleep(5)
            else:
                print(f'  飞书 API 写入失败，已重试 {max_retries} 次: {e}')
                return 0, [], False


def main():
    """主函数"""
    _setup_encoding()

    ap = argparse.ArgumentParser(
        description='微信公众号历史文章 → 飞书多维表格')
    group = ap.add_mutually_exclusive_group(required=False)
    group.add_argument('--his', nargs=2, metavar=('START', 'END'),
                       help='历史批量: --his 20260322 20260331')
    group.add_argument('--update', action='store_true',
                       help='增量更新: 从各公众号 last_update 之后开始')
    group.add_argument('--searchbiz', type=str, metavar='KEYWORD',
                       help='搜索公众号: --searchbiz "DeepTech深科技"')
    ap.add_argument('--list', type=str, default=None,
                    help='指定公众号清单文件（默认 cfg/wxgzh_list.yaml）')
    ap.add_argument('--refresh-cache', action='store_true',
                    help='强制从多维表格全量重建本地 URL 缓存')
    ap.add_argument('--repair-last-update', action='store_true',
                    help='Repair last_update from local success state or bitable')
    args = ap.parse_args()
    if not (args.his or args.update or args.searchbiz or args.repair_last_update):
        ap.error('one of --his/--update/--searchbiz/--repair-last-update is required')

    # --search 模式：搜索公众号后直接退出
    if args.searchbiz:
        creds = _load_secrets_credentials()
        cookie = creds.get('wechat', {}).get('cookie', '')
        if not cookie:
            print('未配置微信 cookie，请更新 ~/.config/secrets/gtokens.yaml 中的 '
                  'wechat.cookie')
            return
        token = _get_mp_token(cookie)
        if not token:
            return
        results = search_biz(args.searchbiz, cookie, token)
        if not results:
            print(f'未找到与 "{args.searchbiz}" 匹配的公众号')
            return
        print(f'\n搜索 "{args.searchbiz}" 结果:\n')
        for r in results:
            alias_str = f'  alias={r["alias"]}' if r['alias'] else ''
            print(f'  {r["nickname"]}{alias_str}')
            print(f'    biz: {r["fakeid"]}')
        return

    print('=' * 60, flush=True)
    print('微信公众号历史文章 → 飞书多维表格', flush=True)
    print('=' * 60, flush=True)

    # 加载公众号清单
    list_file = args.list or DEFAULT_LIST_FILE
    if not os.path.exists(list_file):
        print(f'\n公众号清单文件不存在: {list_file}')
        return
    accounts = load_account_list(list_file)
    if not accounts:
        print('\n公众号清单为空（或所有条目缺少 biz）')
        return
    print(f'\n加载 {len(accounts)} 个公众号', flush=True)

    # 初始化客户端
    for acct in accounts:
        acct['_old_last_update'] = acct.get('last_update', 0)

    client = FeishuClient()
    credentials = {
        'zsxq_token': client.credentials.get(
            'zsxq', {}).get('access_token', ''),
        'zhihu_cookies': client.credentials.get('zhihu', {}),
        'feishu_user_token': client.credentials.get(
            'auth', {}).get('user_access_token', ''),
        'wechat_cookie': client.credentials.get(
            'wechat', {}).get('cookie', ''),
        'xiaobot_token': client.credentials.get(
            'xiaobot', {}).get('token', ''),
    }
    url_parser = UrlParser(credentials=credentials)

    wechat_cookie = credentials.get('wechat_cookie', '')
    mp_token = None
    if args.repair_last_update:
        pass
    elif not wechat_cookie:
        print('\n未配置微信 cookie，尝试从 Chrome 登录态自动获取...', flush=True)
        wechat_cookie = _refresh_wechat_cookie_from_chrome(client)
        if wechat_cookie:
            credentials['wechat_cookie'] = wechat_cookie
            url_parser.credentials['wechat_cookie'] = wechat_cookie

    # 获取微信后台 token
    print('\n获取微信后台 token...', flush=True)
    if args.repair_last_update:
        mp_token = 'repair-skip'
    else:
        print('\nGetting WeChat backend token...', flush=True)
        mp_token = _get_mp_token(wechat_cookie)
        if not mp_token:
            fresh_cookie = _refresh_wechat_cookie_from_chrome(client)
            if fresh_cookie:
                wechat_cookie = fresh_cookie
                credentials['wechat_cookie'] = wechat_cookie
                url_parser.credentials['wechat_cookie'] = wechat_cookie
                mp_token = _get_mp_token(wechat_cookie)
    if not mp_token:
        print('无法获取后台 token，请更新 ~/.config/secrets/gtokens.yaml 中的 '
              'wechat.cookie（需要公众号后台登录态）')
        return
    print(f'后台 token: {mp_token}', flush=True)

    # 检查飞书 token
    if not client.check_token_valid():
        print('\nToken 过期，尝试刷新...')
        if not client.refresh_access_token():
            print('Token 刷新失败，请运行: python src/auth.py')
            return
    print('飞书 Token 有效', flush=True)

    wxgzh_config = client.config.get('wxgzh', {})
    success_state = load_last_update_state()

    if args.repair_last_update:
        print('\n开始修复 last_update...', flush=True)
        source_to_ts = build_bitable_last_update_map(client, wxgzh_config)
        repaired = repair_last_update_from_state(
            accounts, success_state, source_to_ts)
        if repaired:
            save_account_list(list_file, accounts)
            print(f'已修复 {len(repaired)} 个公众号的 last_update:', flush=True)
            for item in repaired:
                old_str = (datetime.fromtimestamp(item['old_ts']).strftime('%Y-%m-%d %H:%M')
                           if item['old_ts'] else '0')
                new_str = datetime.fromtimestamp(item['new_ts']).strftime('%Y-%m-%d %H:%M')
                print(f'  {item["name"]}: {old_str} -> {new_str} ({item["reason"]})',
                      flush=True)
        else:
            print('没有可修复的 last_update，或本地/多维表格缺乏可用依据',
                  flush=True)
        return

    # 初始化 URL 缓存（替代每次全量拉取多维表格）
    bt_cfg = wxgzh_config.get('target_bitable', {})
    app_token = bt_cfg.get('app_token', '')
    table_id = bt_cfg.get('table_id', '')
    url_field = client.config.get('bitable_columns', {}).get('url', '链接')

    cache = BitableUrlCache(table_id, DATA_DIR)
    existing_urls, _ = cache.load()

    if (cache.is_empty() or args.refresh_cache) and app_token and table_id:
        if args.refresh_cache:
            print('\n--refresh-cache: 强制重建本地缓存...', flush=True)
        else:
            print('\n本地缓存为空，从多维表格全量初始化...', flush=True)
        rebuilt = cache.rebuild(client, app_token, table_id, url_field)
        existing_urls, _ = cache.load()
        if rebuilt == 0 and getattr(client, 'last_error', None):
            print('  多维表格缓存重建失败，无法安全去重，终止运行', flush=True)
            print(f'  请先解决多维表格读取权限或恢复本地缓存文件。最近错误: {client.last_error}',
                  flush=True)
            return
    else:
        print(f'\n已从本地缓存加载 {len(existing_urls)} 条 URL', flush=True)

    # 遍历公众号处理
    all_error_rows = []
    if args.repair_last_update:
        print('\n寮€濮嬩慨澶?last_update...', flush=True)
        source_to_ts = build_bitable_last_update_map(client, wxgzh_config)
        repaired = repair_last_update_from_state(
            accounts, success_state, source_to_ts)
        if repaired:
            save_account_list(list_file, accounts)
            print(f'宸蹭慨澶?{len(repaired)} 涓叕浼楀彿鐨?last_update:', flush=True)
            for item in repaired:
                old_str = (datetime.fromtimestamp(item['old_ts']).strftime('%Y-%m-%d %H:%M')
                           if item['old_ts'] else '0')
                new_str = datetime.fromtimestamp(item['new_ts']).strftime('%Y-%m-%d %H:%M')
                print(f'  {item["name"]}: {old_str} -> {new_str} ({item["reason"]})',
                      flush=True)
        else:
            print('娌℃湁鍙慨澶嶇殑 last_update锛屾垨鏈湴/澶氱淮琛ㄦ牸缂轰箯鍙敤渚濇嵁',
                  flush=True)
        return

    total_fetched = 0
    total_written = 0
    _prev_total_fetched = 0

    for acct_idx, acct in enumerate(accounts):
        name = acct['name']
        biz = acct['biz']
        acct['_old_last_update'] = acct.get('last_update', 0)

        # 公众号之间间隔，避免频率限制
        if acct_idx > 0:
            prev_had_articles = (total_fetched > _prev_total_fetched)
            wait = random.uniform(8, 15) if prev_had_articles else random.uniform(2, 4)
            print(f'\n等待 {wait:.0f}s...', flush=True)
            time.sleep(wait)
        _prev_total_fetched = total_fetched

        print(f'\n{"─"*50}', flush=True)
        print(f'公众号: [{acct_idx+1}/{len(accounts)}] {name}', flush=True)

        # 确定时间范围
        if args.his:
            ts_start = date_to_timestamp(args.his[0])
            ts_end = date_to_timestamp(args.his[1], end_of_day=True)
            print(f'日期范围: {args.his[0]} ~ {args.his[1]}')
        else:
            # --update 模式：从 last_update 到当前时间
            last_ts = acct.get('last_update', 0) or 0
            now_ts = int(datetime.now().timestamp())
            ts_start = last_ts + 1  # 严格大于
            ts_end = now_ts
            if last_ts:
                last_str = datetime.fromtimestamp(
                    last_ts).strftime('%Y-%m-%d %H:%M')
                now_str = datetime.fromtimestamp(
                    now_ts).strftime('%Y-%m-%d %H:%M')
                print(f'增量更新: {last_str} → {now_str}')
            else:
                now_str = datetime.fromtimestamp(
                    now_ts).strftime('%Y-%m-%d %H:%M')
                print(f'增量更新: 首次运行 → {now_str}')

        # 阶段一：抓取文章列表
        print(f'\n阶段一：抓取文章列表', flush=True)
        articles, fetch_errors = fetch_articles(
            biz, wechat_cookie, mp_token, ts_start, ts_end, name)
        all_error_rows.extend(fetch_errors)

        if not articles:
            print(f'  未抓取到文章')
            continue

        # 按发布时间从远到近排序
        articles.sort(key=lambda a: a['publish_ts'])
        print(f'  抓取到 {len(articles)} 篇文章', flush=True)
        total_fetched += len(articles)

        # 阶段二：解析文章
        print(f'\n阶段二：解析文章', flush=True)
        parsed_rows, parse_errors = parse_articles(
            url_parser, articles, name)
        all_error_rows.extend(parse_errors)

        # 阶段三：写入多维表格
        print(f'\n阶段三：写入多维表格', flush=True)
        written, cache_entries, write_ok = write_to_bitable(
            client, parsed_rows, wxgzh_config, existing_urls)
        total_written += written

        # 追加到本地缓存
        if cache_entries:
            cache.append(cache_entries)
            print(f'  追加 {len(cache_entries)} 条 → {os.path.basename(cache._file)}')

        # 更新 last_update
        if articles and write_ok:
            max_success_ts = max(a['publish_ts'] for a in articles)
            if args.his:
                # --his 模式：更新为本批次最新文章时间戳
                new_ts = max(a['publish_ts'] for a in articles)
            else:
                # --update 模式：更新为本次运行时间（ts_end）
                new_ts = ts_end
            old_ts = acct.get('last_update', 0) or 0
            if new_ts > old_ts:
                acct['last_update'] = new_ts
                ts_str = datetime.fromtimestamp(
                    new_ts).strftime('%Y-%m-%d %H:%M')
                print(f'  更新 last_update: {new_ts} ({ts_str})')
            record_successful_account_update(success_state, acct, max_success_ts)
        elif articles:
            print('  未更新 last_update，原因：多维表格写入未完成')

    # 保存清单文件（更新 last_update）
    updated = [a for a in accounts
               if a.get('last_update') != a.get('_old_last_update')]
    if updated:
        save_account_list(list_file, accounts)
        print(f'\n已更新 {len(updated)} 个公众号的 last_update')

    # 写入错误日志
    if success_state:
        save_last_update_state(success_state)

    if all_error_rows:
        append_error_log(all_error_rows)
        print(f'\n错误日志已写入: {ERROR_LOG_FILE} '
              f'({len(all_error_rows)} 条)')

    # 汇总
    print(f'\n{"="*60}', flush=True)
    print(f'处理完成: 抓取 {total_fetched} 篇，'
          f'写入 {total_written} 条', flush=True)


if __name__ == '__main__':
    main()
