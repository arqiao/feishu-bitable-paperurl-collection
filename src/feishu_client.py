"""
飞书 API 客户端
提供飞书 API 的封装，包括授权、消息读取、文档操作等功能
"""

import os
import requests
import time
import json
import yaml
try:
    from secrets_loader import load as _secrets_load
except ImportError:
    import sys
    sys.path.insert(0, "/Volumes/DATADRIVE/workspace/sys")
    from secrets_loader import load as _secrets_load
from typing import Dict, List, Optional, Any
from datetime import datetime

# 默认配置文件路径（相对于项目根目录）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG = os.path.join(_PROJECT_ROOT, 'cfg', 'config.yaml')
_DEFAULT_CREDENTIALS = os.path.join(_PROJECT_ROOT, 'cfg', 'credentials.yaml')
_PROJECT_AUTH_KEY = "auth_feishuMSG-xls"


class FeishuClient:
    """飞书 API 客户端"""

    # token 过期相关错误码
    TOKEN_EXPIRED_CODES = {99991677, 99991668, 99991664}
    INVALID_REFRESH_TOKEN_CODES = {10012, 10014, 20026, 99991663}
    # 额度耗尽错误码（不应重试）
    QUOTA_EXHAUSTED_CODES = {99991403}
    # 限流错误码（不应立即重试）
    RATE_LIMIT_CODES = {429, 99991400}

    def __init__(self, config_path: str = None,
                 credentials_path: str = None):
        """初始化客户端

        Args:
            config_path: 业务配置文件路径
            credentials_path: 凭证配置文件路径
        """
        self.config_path = config_path or _DEFAULT_CONFIG
        self.credentials_path = credentials_path or _DEFAULT_CREDENTIALS
        self.config = self._load_yaml(self.config_path)
        self.credentials = _secrets_load("gkeys", "gfeishu", "gtokens")
        self.base_url = "https://open.feishu.cn/open-apis"
        self._session = requests.Session()
        self._session.trust_env = False
        self.last_error = None
        self._tenant_token_cache = None
        self._tenant_token_expire = 0
        self._bitable_read_auth_mode = {}
        self._bitable_write_auth_mode = {}
        self._retryable_api_codes = {1254607}

    @staticmethod
    def _load_yaml(path: str) -> Dict:
        """加载 YAML 文件"""
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}

    def _save_config(self):
        """Deprecated: whole-file YAML rewrite drops comments; use precise updaters instead."""
        raise RuntimeError(
            "_save_config() 已停用：它会整文件重写 config.yaml 并丢失注释，请改用精确定位写回"
        )

    def _save_credentials(self):
        """保存本项目 token — 写入 ~/.config/secrets/gfeishu.yaml"""
        from pathlib import Path
        path = Path.home() / ".config" / "secrets" / "gfeishu.yaml"
        current = {}
        if path.exists():
            current = self._load_yaml(str(path))
        current[_PROJECT_AUTH_KEY] = self.credentials[_PROJECT_AUTH_KEY]
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(current, f, allow_unicode=True, default_flow_style=False)

    def _get_headers(self, use_user_token: bool = True) -> Dict[str, str]:
        """获取请求头"""
        if use_user_token:
            token = self.credentials[_PROJECT_AUTH_KEY]['user_access_token']
        else:
            token = self._get_tenant_access_token() or ''
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def _is_forbidden_error(self) -> bool:
        """Check whether the last API error is a permission denial."""
        err = (self.last_error or '').lower()
        return 'forbidden' in err or '91403' in err

    def _get_bitable_read_auth_mode(self, app_token: str, table_id: str) -> str:
        """Return cached auth mode for bitable reads; default to user first."""
        return self._bitable_read_auth_mode.get((app_token, table_id), 'user')

    def _set_bitable_read_auth_mode(self, app_token: str, table_id: str, mode: str):
        """Remember which auth mode works for bitable reads on this table."""
        self._bitable_read_auth_mode[(app_token, table_id)] = mode

    def _get_bitable_write_auth_mode(self, app_token: str, table_id: str) -> str:
        """Return cached auth mode for bitable writes; default to user first."""
        return self._bitable_write_auth_mode.get((app_token, table_id), 'user')

    def _set_bitable_write_auth_mode(self, app_token: str, table_id: str, mode: str):
        """Remember which auth mode works for bitable writes on this table."""
        self._bitable_write_auth_mode[(app_token, table_id)] = mode

    def check_token_valid(self) -> bool:
        """检查 token 是否有效"""
        expire_time = self.credentials[_PROJECT_AUTH_KEY].get('user_token_expire_time', 0)
        try:
            expire_time = int(expire_time)
        except (TypeError, ValueError):
            return False
        current_time = int(time.time())
        # 提前 5 分钟判断过期
        return current_time < (expire_time - 300)

    def _request(self, method: str, endpoint: str, use_user_token: bool = True,
                 max_retries: int = 3, silent: bool = False, **kwargs) -> Optional[Dict]:
        """统一请求方法（带重试和 token 自动刷新）

        Args:
            method: HTTP 方法 (GET/POST/DELETE)
            endpoint: API 端点路径
            use_user_token: 是否使用用户 token
            max_retries: 最大重试次数
            silent: 是否静默模式（不打印错误信息，由调用方处理）
            **kwargs: 传递给 requests 的其他参数

        Returns:
            dict: API 返回的 data 字段，失败返回 None
        """
        url = f"{self.base_url}{endpoint}"
        self.last_error = None  # 记录最后一次错误
        kwargs.setdefault('timeout', 30)

        for attempt in range(max_retries):
            headers = self._get_headers(use_user_token)
            kwargs['headers'] = headers

            try:
                response = self._session.request(method, url, **kwargs)
                result = response.json()

                if result.get('code') == 0:
                    return result.get('data', {})

                # 额度耗尽，立即返回不重试
                if result.get('code') in self.QUOTA_EXHAUSTED_CODES:
                    error_msg = "API 额度已耗尽，本月无法继续调用"
                    self.last_error = error_msg
                    if not silent:
                        print(f"  ✗ {error_msg}")
                    return None

                # 限流，立即返回不重试
                if result.get('code') in self.RATE_LIMIT_CODES:
                    error_msg = "触发限流，请稍后重试"
                    self.last_error = error_msg
                    if not silent:
                        print(f"  ⚠ {error_msg}")
                    return None

                # token 过期，尝试刷新后重试
                if result.get('code') in self.TOKEN_EXPIRED_CODES:
                    if not silent:
                        print(f"  Token 已过期，尝试自动刷新...")
                    if self.refresh_access_token():
                        continue  # 刷新成功，重试当前请求
                    error_msg = "Token 刷新失败，请重新授权"
                    self.last_error = error_msg
                    if not silent:
                        print(f"  {error_msg}")
                    return None

                error_msg = f"{result.get('msg')} (code: {result.get('code')})"
                self.last_error = error_msg
                if result.get('code') in self._retryable_api_codes:
                    if not silent:
                        print(f"API错误: {error_msg}")
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    return None
                if not silent:
                    print(f"API错误: {error_msg}")
                return None

            except Exception as e:
                error_msg = f"请求异常 (第{attempt + 1}次): {e}"
                self.last_error = str(e)
                if not silent:
                    print(error_msg)
                if attempt < max_retries - 1:
                    time.sleep(2)

        return None

    def refresh_access_token(self) -> bool:
        """刷新 access token

        使用 refresh_token 刷新用户访问令牌
        如果 refresh_token 也过期，需要重新授权

        注意：由于飞书 API 的限制，refresh_token 有效期为 30 天
        超过 30 天未使用需要重新授权
        """
        refresh_token = self.credentials[_PROJECT_AUTH_KEY].get('user_refresh_token', '')
        if not refresh_token:
            print("刷新 token 失败: 缺少 refresh_token")
            return False

        url = f"{self.base_url}/authen/v1/refresh_access_token"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "app_id": self.credentials['feishu']['app_id'],
            "app_secret": self.credentials['feishu']['app_secret']
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }

        try:
            response = self._session.post(url, json=data, headers=headers)
            result = response.json()

            if result.get('code') == 0:
                # 保存新的 token
                self.credentials[_PROJECT_AUTH_KEY]['user_access_token'] = result['data']['access_token']
                self.credentials[_PROJECT_AUTH_KEY]['user_refresh_token'] = result['data']['refresh_token']
                self.credentials[_PROJECT_AUTH_KEY]['user_token_expire_time'] = int(time.time()) + result['data']['expires_in']
                self._save_credentials()
                print(f"✓ Token 刷新成功，有效期: {result['data']['expires_in'] / 3600:.1f} 小时")
                return True
            else:
                error_msg = result.get('msg', '未知错误')
                error_code = result.get('code', 'unknown')
                print(f"刷新 token 失败 (code: {error_code}): {error_msg}")

                # 如果是 refresh_token 过期，提示重新授权
                if error_code in self.INVALID_REFRESH_TOKEN_CODES:
                    print("提示: refresh_token 已失效或已被使用，需要重新授权")

                return False
        except Exception as e:
            print(f"刷新 token 异常: {e}")
            return False

    def get_chat_list(self) -> List[Dict]:
        """获取群聊列表"""
        all_chats = []
        page_token = None

        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token

            data = self._request("GET", "/im/v1/chats", params=params)
            if data is None:
                break

            items = data.get('items', [])
            all_chats.extend(items)

            page_token = data.get('page_token')
            if not page_token:
                break

        return all_chats

    def find_chat_by_name(self, chat_name: str) -> Optional[str]:
        """根据群名查找群聊 ID"""
        chats = self.get_chat_list()
        for chat in chats:
            if chat.get('name') == chat_name:
                return chat.get('chat_id')
        return None

    def get_chat_messages(self, chat_id: str, start_time: int = 0) -> List[Dict]:
        """获取群聊消息 - 使用应用身份"""
        return self._get_messages_as_app(chat_id, start_time)

    def _get_messages_as_user(self, chat_id: str, start_time: int = 0) -> Optional[List[Dict]]:
        """使用用户身份获取消息"""
        all_messages = []
        page_token = None

        while True:
            params = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": 50
            }
            if page_token:
                params["page_token"] = page_token

            data = self._request("GET", "/im/v1/messages", params=params)
            if data is None:
                return None

            items = data.get('items', [])
            for msg in items:
                msg_time = int(msg.get('create_time', '0'))
                if msg_time > start_time:
                    all_messages.append(msg)

            page_token = data.get('page_token')
            if not page_token:
                break

        return all_messages

    def _get_messages_as_app(self, chat_id: str, start_time: int = 0) -> List[Dict]:
        """使用应用身份获取消息"""
        # 获取应用 access token
        app_token = self._get_tenant_access_token()
        if not app_token:
            return []

        url = f"{self.base_url}/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        all_messages = []
        page_token = None

        while True:
            params = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": 50
            }
            if start_time > 0:
                # 飞书消息接口的 start_time/end_time 使用秒级时间戳；
                # 本项目内部 last_processed_time 使用毫秒级时间戳。
                start_time_s = int(start_time / 1000) if start_time > 10**12 else int(start_time)
                params["start_time"] = str(start_time_s)
                params["end_time"] = str(int(time.time()))
            if page_token:
                params["page_token"] = page_token

            response = self._session.get(url, headers=headers, params=params, timeout=60)
            result = response.json()

            if result.get('code') != 0:
                print(f"应用身份获取消息失败: {result.get('msg')}")
                return []

            items = result.get('data', {}).get('items', [])

            # 收集所有消息，不要提前返回
            for msg in items:
                msg_time = int(msg.get('create_time', '0'))
                if msg_time > start_time:
                    all_messages.append(msg)

            page_token = result.get('data', {}).get('page_token')
            if not page_token:
                break

        return all_messages

    def _get_tenant_access_token(self) -> Optional[str]:
        """获取应用 access token（带缓存，有效期 2 小时）"""
        # 缓存有效则直接返回（提前 5 分钟刷新）
        if self._tenant_token_cache and time.time() < self._tenant_token_expire - 300:
            return self._tenant_token_cache

        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        data = {
            "app_id": self.credentials['feishu']['app_id'],
            "app_secret": self.credentials['feishu']['app_secret']
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }

        try:
            response = self._session.post(url, json=data, headers=headers)
            result = response.json()

            if result.get('code') == 0:
                self._tenant_token_cache = result['tenant_access_token']
                self._tenant_token_expire = time.time() + result.get('expire', 7200)
                return self._tenant_token_cache
            else:
                print(f"获取应用 token 失败: {result.get('msg')}")
                return None
        except (ConnectionError, requests.exceptions.ConnectionError) as e:
            print(f"获取应用 token 连接异常，5秒后重试: {e}")
            time.sleep(5)
            try:
                response = self._session.post(url, json=data, headers=headers)
                result = response.json()
                if result.get('code') == 0:
                    self._tenant_token_cache = result['tenant_access_token']
                    self._tenant_token_expire = time.time() + result.get('expire', 7200)
                    return self._tenant_token_cache
                else:
                    print(f"获取应用 token 重试失败: {result.get('msg')}")
                    return None
            except Exception as e2:
                print(f"获取应用 token 重试异常: {e2}")
                return None
        except Exception as e:
            print(f"获取应用 token 异常: {e}")
            return None

    def get_bitable_tables(self, app_token: str) -> List[Dict]:
        """获取多维表格的所有表格"""
        data = self._request("GET", f"/bitable/v1/apps/{app_token}/tables",
                             use_user_token=False)
        if data is None:
            return []
        return data.get('items', [])

    def get_bitable_views(self, app_token: str, table_id: str) -> List[Dict]:
        """获取表格的所有视图"""
        data = self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
                             use_user_token=False)
        if data is None:
            return []
        return data.get('items', [])

    def add_bitable_record(self, app_token: str, table_id: str, fields: Dict) -> bool:
        """添加多维表格记录（单条）"""
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            json={"fields": fields}
        )
        return data is not None

    def batch_add_bitable_records(self, app_token: str, table_id: str,
                                   records: List[Dict]) -> Dict:
        """批量添加多维表格记录（最多 500 条/次）

        Args:
            app_token: 多维表格 app_token
            table_id: 表格 ID
            records: 记录列表，每条记录为 fields 字典

        Returns:
            dict: {"success": 成功数, "failed": 失败数, "errors": [...],
                   "records": [创建的记录（含 record_id）]}
        """
        if not records:
            return {"success": 0, "failed": 0, "errors": [], "records": []}

        # 飞书 API 单次最多 500 条
        batch_size = 500
        total_success = 0
        total_failed = 0
        errors = []
        auth_mode = self._get_bitable_write_auth_mode(app_token, table_id)
        all_created = []

        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            payload = {"records": [{"fields": r} for r in batch]}

            data = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                json=payload,
                use_user_token=(auth_mode == 'user')
            )

            if data is None and auth_mode == 'user' and self._is_forbidden_error():
                print("  多维表格写入被拒绝，改用 tenant_access_token 重试...", flush=True)
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                    json=payload,
                    use_user_token=False
                )
                if data is not None:
                    auth_mode = 'tenant'
                    self._set_bitable_write_auth_mode(app_token, table_id, 'tenant')
                    print("  tenant_access_token 重试成功，继续写入多维表格...", flush=True)
            elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
                print("  多维表格写入被拒绝，改用 user_access_token 重试...", flush=True)
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                    json=payload,
                    use_user_token=True
                )
                if data is not None:
                    auth_mode = 'user'
                    self._set_bitable_write_auth_mode(app_token, table_id, 'user')
                    print("  user_access_token 重试成功，继续写入多维表格...", flush=True)
            elif data is not None:
                self._set_bitable_write_auth_mode(app_token, table_id, auth_mode)

            if data is not None:
                created = data.get('records', [])
                total_success += len(created)
                all_created.extend(created)
            else:
                total_failed += len(batch)
                errors.append(self.last_error or "批量写入失败")

        return {"success": total_success, "failed": total_failed,
                "errors": errors, "records": all_created}

    def update_bitable_record(self, app_token: str, table_id: str,
                              record_id: str, fields: Dict) -> bool:
        """更新多维表格记录"""
        auth_mode = self._get_bitable_write_auth_mode(app_token, table_id)
        data = self._request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            json={"fields": fields},
            use_user_token=(auth_mode == 'user')
        )
        if data is None and auth_mode == 'user' and self._is_forbidden_error():
            print("  多维表格更新被拒绝，改用 tenant_access_token 重试...", flush=True)
            data = self._request(
                "PUT",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                json={"fields": fields},
                use_user_token=False
            )
            if data is not None:
                self._set_bitable_write_auth_mode(app_token, table_id, 'tenant')
                print("  tenant_access_token 重试成功，继续更新多维表格...", flush=True)
        elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
            print("  多维表格更新被拒绝，改用 user_access_token 重试...", flush=True)
            data = self._request(
                "PUT",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                json={"fields": fields},
                use_user_token=True
            )
            if data is not None:
                self._set_bitable_write_auth_mode(app_token, table_id, 'user')
                print("  user_access_token 重试成功，继续更新多维表格...", flush=True)
        elif data is not None:
            self._set_bitable_write_auth_mode(app_token, table_id, auth_mode)
        return data is not None

    def batch_update_bitable_records(self, app_token: str, table_id: str,
                                     records: List[Dict]) -> Dict:
        """批量更新多维表格记录（最多 500 条/次）

        Args:
            app_token: 多维表格 app_token
            table_id: 表格 ID
            records: [{"record_id": "rec...", "fields": {...}}, ...]

        Returns:
            dict: {"success": 成功数, "failed": 失败数, "errors": [...],
                   "failed_records": [...]}
        """
        if not records:
            return {"success": 0, "failed": 0, "errors": [], "failed_records": []}

        batch_size = 500
        total_success = 0
        total_failed = 0
        errors = []
        failed_records = []
        auth_mode = self._get_bitable_write_auth_mode(app_token, table_id)

        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            payload = {"records": batch}

            data = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}"
                f"/records/batch_update",
                json=payload,
                use_user_token=(auth_mode == 'user')
            )

            if data is None and auth_mode == 'user' and self._is_forbidden_error():
                print("  多维表格批量更新被拒绝，改用 tenant_access_token 重试...", flush=True)
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}"
                    f"/records/batch_update",
                    json=payload,
                    use_user_token=False
                )
                if data is not None:
                    auth_mode = 'tenant'
                    self._set_bitable_write_auth_mode(app_token, table_id, 'tenant')
                    print("  tenant_access_token 重试成功，继续批量更新多维表格...", flush=True)
            elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
                print("  多维表格批量更新被拒绝，改用 user_access_token 重试...", flush=True)
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}"
                    f"/records/batch_update",
                    json=payload,
                    use_user_token=True
                )
                if data is not None:
                    auth_mode = 'user'
                    self._set_bitable_write_auth_mode(app_token, table_id, 'user')
                    print("  user_access_token 重试成功，继续批量更新多维表格...", flush=True)
            elif data is not None:
                self._set_bitable_write_auth_mode(app_token, table_id, auth_mode)

            if data is not None:
                updated = data.get('records', [])
                total_success += len(updated)
            else:
                last_error = self.last_error or "批量更新失败"
                if len(batch) > 1 and ('record not found' in last_error or '1254043' in last_error):
                    mid = len(batch) // 2
                    left = self.batch_update_bitable_records(app_token, table_id, batch[:mid])
                    right = self.batch_update_bitable_records(app_token, table_id, batch[mid:])
                    total_success += left.get('success', 0) + right.get('success', 0)
                    total_failed += left.get('failed', 0) + right.get('failed', 0)
                    errors.extend(left.get('errors', []))
                    errors.extend(right.get('errors', []))
                    failed_records.extend(left.get('failed_records', []))
                    failed_records.extend(right.get('failed_records', []))
                else:
                    total_failed += len(batch)
                    errors.append(last_error)
                    for record in batch:
                        failed = dict(record)
                        failed['error'] = last_error
                        failed_records.append(failed)

        return {"success": total_success, "failed": total_failed,
                "errors": errors, "failed_records": failed_records}

    def batch_delete_bitable_records(self, app_token: str, table_id: str,
                                     record_ids: List[str]) -> bool:
        """批量删除多维表格记录（最多 500 条/次）。"""
        if not record_ids:
            return True

        batch_size = 500
        auth_mode = self._get_bitable_write_auth_mode(app_token, table_id)

        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i:i + batch_size]
            payload = {"records": batch}

            data = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}"
                f"/records/batch_delete",
                json=payload,
                use_user_token=(auth_mode == 'user')
            )
            if data is None and auth_mode == 'user' and self._is_forbidden_error():
                print("  多维表格删除被拒绝，改用 tenant_access_token 重试...", flush=True)
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}"
                    f"/records/batch_delete",
                    json=payload,
                    use_user_token=False
                )
                if data is not None:
                    auth_mode = 'tenant'
                    self._set_bitable_write_auth_mode(app_token, table_id, 'tenant')
                    print("  tenant_access_token 重试成功，继续删除多维表格记录...", flush=True)
            elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
                print("  多维表格删除被拒绝，改用 user_access_token 重试...", flush=True)
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}"
                    f"/records/batch_delete",
                    json=payload,
                    use_user_token=True
                )
                if data is not None:
                    auth_mode = 'user'
                    self._set_bitable_write_auth_mode(app_token, table_id, 'user')
                    print("  user_access_token 重试成功，继续删除多维表格记录...", flush=True)
            elif data is not None:
                self._set_bitable_write_auth_mode(app_token, table_id, auth_mode)

            if data is None:
                return False

        return True

    def get_wiki_node_info(self, wiki_token: str) -> Optional[str]:
        """通过 wiki API 获取知识库节点的实际 obj_token（bitable app_token）

        Args:
            wiki_token: wiki 节点 token

        Returns:
            str: 实际的 bitable app_token，失败返回 None
        """
        data = self._request("GET", "/wiki/v2/spaces/get_node", params={"token": wiki_token})
        if data is None:
            return None

        node = data.get('node', {})
        obj_token = node.get('obj_token', '')
        if obj_token:
            return obj_token
        print(f"wiki 节点未返回 obj_token")
        return None

    def get_bitable_records(self, app_token: str, table_id: str,
                            field_names: List[str]) -> List[Dict]:
        """分页读取多维表格所有记录

        Args:
            app_token: 多维表格 app_token
            table_id: 表格 ID
            field_names: 需要返回的字段名列表

        Returns:
            list: 记录列表，每条记录为 fields 字典
        """
        all_records = []
        page_token = None
        auth_mode = self._get_bitable_read_auth_mode(app_token, table_id)

        while True:
            params = {"page_size": 500}
            if field_names:
                params["field_names"] = json.dumps(field_names, ensure_ascii=False)
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=params,
                use_user_token=(auth_mode == 'user'),
                max_retries=5,
                timeout=60
            )
            if data is None and auth_mode == 'user' and self._is_forbidden_error():
                print("  多维表格读取被拒绝，改用 tenant_access_token 重试...", flush=True)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                    use_user_token=False,
                    max_retries=5,
                    timeout=60
                )
                if data is not None:
                    auth_mode = 'tenant'
                    self._set_bitable_read_auth_mode(app_token, table_id, 'tenant')
                    print("  tenant_access_token 重试成功，继续读取多维表格...", flush=True)
            elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
                print("  多维表格读取被拒绝，改用 user_access_token 重试...", flush=True)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                    use_user_token=True,
                    max_retries=5,
                    timeout=60
                )
                if data is not None:
                    auth_mode = 'user'
                    self._set_bitable_read_auth_mode(app_token, table_id, 'user')
                    print("  user_access_token 重试成功，继续读取多维表格...", flush=True)
            elif data is not None:
                self._set_bitable_read_auth_mode(app_token, table_id, auth_mode)
            if data is None:
                break

            items = data.get('items', [])
            for item in items:
                fields = item.get('fields', {})
                fields['_record_id'] = item.get('record_id', '')
                all_records.append(fields)

            page_token = data.get('page_token')
            if not page_token:
                break

        return all_records

    def get_raw_bitable_records(self, app_token: str, table_id: str,
                                field_names: List[str] = None) -> List[Dict]:
        """分页读取多维表格原始记录 items，保留 record_id 和 fields 结构。"""
        all_records = []
        page_token = None
        auth_mode = self._get_bitable_read_auth_mode(app_token, table_id)

        while True:
            params = {"page_size": 500}
            if field_names:
                params["field_names"] = json.dumps(field_names, ensure_ascii=False)
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=params,
                use_user_token=(auth_mode == 'user'),
                max_retries=5,
                timeout=60
            )
            if data is None and auth_mode == 'user' and self._is_forbidden_error():
                print("  多维表格读取被拒绝，改用 tenant_access_token 重试...", flush=True)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                    use_user_token=False,
                    max_retries=5,
                    timeout=60
                )
                if data is not None:
                    auth_mode = 'tenant'
                    self._set_bitable_read_auth_mode(app_token, table_id, 'tenant')
                    print("  tenant_access_token 重试成功，继续读取多维表格...", flush=True)
            elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
                print("  多维表格读取被拒绝，改用 user_access_token 重试...", flush=True)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                    use_user_token=True,
                    max_retries=5,
                    timeout=60
                )
                if data is not None:
                    auth_mode = 'user'
                    self._set_bitable_read_auth_mode(app_token, table_id, 'user')
                    print("  user_access_token 重试成功，继续读取多维表格...", flush=True)
            elif data is not None:
                self._set_bitable_read_auth_mode(app_token, table_id, auth_mode)
            if data is None:
                break

            all_records.extend(data.get('items', []))
            page_token = data.get('page_token')
            if not page_token:
                break

        return all_records

    def search_bitable_records(self, app_token: str, table_id: str,
                                field_names: List[str],
                                filter_str: str = '') -> List[Dict]:
        """按条件搜索多维表格记录（带 filter，节省 API 调用）

        Args:
            app_token: 多维表格 app_token
            table_id: 表格 ID
            field_names: 需要返回的字段名列表
            filter_str: 飞书 filter 表达式，如 'CurrentValue.[精选合集].contains("WTA")'

        Returns:
            list: 记录列表，每条记录为 fields 字典（含 _record_id）
        """
        all_records = []
        page_token = None
        auth_mode = self._get_bitable_read_auth_mode(app_token, table_id)

        while True:
            params = {"page_size": 500}
            if field_names:
                params["field_names"] = json.dumps(field_names, ensure_ascii=False)
            if filter_str:
                params["filter"] = filter_str
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=params,
                use_user_token=(auth_mode == 'user'),
                max_retries=5,
                timeout=60
            )
            if data is None and auth_mode == 'user' and self._is_forbidden_error():
                print("  多维表格搜索被拒绝，改用 tenant_access_token 重试...", flush=True)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                    use_user_token=False,
                    max_retries=5,
                    timeout=60
                )
                if data is not None:
                    auth_mode = 'tenant'
                    self._set_bitable_read_auth_mode(app_token, table_id, 'tenant')
                    print("  tenant_access_token 重试成功，继续搜索多维表格...", flush=True)
            elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
                print("  多维表格搜索被拒绝，改用 user_access_token 重试...", flush=True)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                    use_user_token=True,
                    max_retries=5,
                    timeout=60
                )
                if data is not None:
                    auth_mode = 'user'
                    self._set_bitable_read_auth_mode(app_token, table_id, 'user')
                    print("  user_access_token 重试成功，继续搜索多维表格...", flush=True)
            elif data is not None:
                self._set_bitable_read_auth_mode(app_token, table_id, auth_mode)
            if data is None:
                break

            items = data.get('items', [])
            for item in items:
                fields = item.get('fields', {})
                fields['_record_id'] = item.get('record_id', '')
                all_records.append(fields)

            page_token = data.get('page_token')
            if not page_token:
                break

        return all_records

    def get_bitable_fields(self, app_token: str, table_id: str) -> List[Dict]:
        """获取表格的字段信息"""
        auth_mode = self._get_bitable_read_auth_mode(app_token, table_id)
        data = self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                             use_user_token=(auth_mode == 'user'))
        if data is None and auth_mode == 'user' and self._is_forbidden_error():
            print("  多维表格字段读取被拒绝，改用 tenant_access_token 重试...", flush=True)
            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                use_user_token=False
            )
            if data is not None:
                self._set_bitable_read_auth_mode(app_token, table_id, 'tenant')
                print("  tenant_access_token 重试成功，继续读取多维表格字段...", flush=True)
        elif data is None and auth_mode == 'tenant' and self._is_forbidden_error():
            print("  多维表格字段读取被拒绝，改用 user_access_token 重试...", flush=True)
            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                use_user_token=True
            )
            if data is not None:
                self._set_bitable_read_auth_mode(app_token, table_id, 'user')
                print("  user_access_token 重试成功，继续读取多维表格字段...", flush=True)
        elif data is not None:
            self._set_bitable_read_auth_mode(app_token, table_id, auth_mode)
        if data is None:
            return []
        return data.get('items', [])

    def get_pin_messages(self, chat_id: str) -> List[str]:
        """获取群内 Pin 消息的 message_id 列表

        Returns:
            list: 被 Pin 的 message_id 列表
        """
        app_token = self._get_tenant_access_token()
        if not app_token:
            return []

        url = f"{self.base_url}/im/v1/pins"
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        pin_message_ids = []
        page_token = None

        while True:
            params = {"chat_id": chat_id, "page_size": 50}
            if page_token:
                params["page_token"] = page_token

            try:
                response = requests.get(url, headers=headers, params=params)
                result = response.json()

                if result.get('code') != 0:
                    print(f"获取 Pin 消息失败: {result.get('msg')}")
                    break

                items = result.get('data', {}).get('items', [])
                for item in items:
                    msg_id = item.get('message_id', '')
                    if msg_id:
                        pin_message_ids.append(msg_id)

                page_token = result.get('data', {}).get('page_token')
                if not page_token:
                    break
            except Exception as e:
                print(f"获取 Pin 消息异常: {e}")
                break

        return pin_message_ids

    # 电子表格相关方法
    def get_spreadsheet_info(self, spreadsheet_token: str) -> Optional[Dict]:
        """获取电子表格信息"""
        data = self._request("GET", f"/sheets/v3/spreadsheets/{spreadsheet_token}")
        if data is None:
            return None
        return data.get('spreadsheet', {})

    def get_spreadsheet_sheets(self, spreadsheet_token: str) -> List[Dict]:
        """获取电子表格的所有工作表"""
        data = self._request("GET", f"/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query")
        if data is None:
            return []
        return data.get('sheets', [])

    def append_spreadsheet_values(self, spreadsheet_token: str, sheet_id: str,
                                   values: List[List], max_retries: int = 3) -> bool:
        """向电子表格追加数据（失败自动重试）"""
        payload = {
            "valueRange": {
                "range": f"{sheet_id}!A:Z",
                "values": values
            }
        }
        data = self._request(
            "POST",
            f"/sheets/v2/spreadsheets/{spreadsheet_token}/values_append",
            max_retries=max_retries,
            json=payload
        )
        return data is not None

    def get_spreadsheet_column_values(self, spreadsheet_token: str, sheet_id: str,
                                       col_letter: str) -> List[str]:
        """读取电子表格某一列的所有值（跳过表头）

        Args:
            spreadsheet_token: 电子表格 token
            sheet_id: 工作表 ID
            col_letter: 列字母，如 'E'

        Returns:
            list: 该列所有非空值
        """
        range_str = f"{col_letter}2:{col_letter}5000"
        data = self._request(
            "GET",
            f"/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}!{range_str}"
        )
        if data is None:
            return []

        rows = data.get('valueRange', {}).get('values', [])
        values = []
        for row in rows:
            if row and row[0]:
                cell = row[0]
                # 飞书表格中的超链接单元格返回的是列表结构
                if isinstance(cell, list):
                    for item in cell:
                        if isinstance(item, dict) and item.get('link'):
                            values.append(item['link'].strip())
                            break
                else:
                    values.append(str(cell).strip())
        return values

    def get_spreadsheet_values(self, spreadsheet_token: str, sheet_id: str,
                                range_str: str = "A1:Z1") -> List[List]:
        """读取电子表格数据（用于获取表头）"""
        data = self._request(
            "GET",
            f"/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}!{range_str}"
        )
        if data is None:
            return []
        return data.get('valueRange', {}).get('values', [])

    def recall_message(self, message_id: str, show_detail: bool = False) -> bool:
        """撤回消息

        Args:
            message_id: 消息 ID
            show_detail: 是否显示详细的API响应信息

        Returns:
            bool: 撤回是否成功

        注意：
            - 只能撤回自己发送的消息
            - 或者需要是群管理员才能撤回群内其他人的消息
            - 撤回后群里会显示"XXX撤回了一条消息"的提示
            - 这是飞书API的设计，无法真正删除消息而不留痕迹
        """
        data = self._request("DELETE", f"/im/v1/messages/{message_id}")
        if show_detail:
            print(f"  API响应: {data}")
        return data is not None
