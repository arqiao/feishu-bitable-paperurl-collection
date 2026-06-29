"""Small Knowledge Planet API adapter used by downloader scripts."""

import time

import requests

from modules.http_diagnostics import classify_http_failure


ZSXQ_CREDENTIAL_HINT = (
    '请更新 ~/.config/secrets/gtokens.yaml 中的 zsxq.access_token'
)

ZSXQ_HEADERS_TPL = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://wx.zsxq.com/',
}


def zsxq_headers(token):
    headers = dict(ZSXQ_HEADERS_TPL)
    headers['Cookie'] = f'zsxq_access_token={token}'
    return headers


def diagnose_zsxq_failure(data=None, exc=None, status_code=None):
    """Return a user-facing reason and whether retry is recommended."""
    if exc is not None and isinstance(exc, ValueError):
        return f'响应解析失败：{exc}', True

    diag = classify_http_failure(
        error=exc,
        status_code=status_code,
        payload=data,
        service='知识星球',
        credential_hint=ZSXQ_CREDENTIAL_HINT,
    )

    if diag.category == 'auth':
        return f'{diag.summary}：当前 zsxq.access_token 无效或已过期；{diag.action}', False
    if diag.category == 'permission':
        return (
            f'{diag.summary}：请确认当前账号仍有星球成员权限，'
            '并确认 group_url/group_id 配置正确'
        ), False
    if diag.category in ('rate_limit', 'temporary', 'server_error'):
        return _with_detail(diag.summary, diag.detail), True
    if diag.category in ('timeout', 'network'):
        return _with_detail(diag.summary, diag.detail), True
    if diag.category == 'unknown':
        return _api_failure_reason(data), True
    return _with_detail(diag.summary, diag.detail), diag.retryable


def fetch_zsxq_json_with_retry(session, url, token, operation_label,
                               timeout=15, max_attempts=5,
                               sleep_func=time.sleep, printer=print):
    """GET a Knowledge Planet JSON endpoint with platform-aware retries."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(url, headers=zsxq_headers(token),
                               timeout=timeout)
            data = resp.json()
            if data.get('succeeded'):
                if attempt > 1:
                    printer(
                        f'  {operation_label}重试成功'
                        f'（尝试 {attempt}/{max_attempts}）'
                    )
                return data
            reason, retryable = diagnose_zsxq_failure(
                data=data, status_code=getattr(resp, 'status_code', None))
        except Exception as error:
            reason, retryable = diagnose_zsxq_failure(exc=error)

        if not retryable:
            printer(f'  {operation_label}失败（尝试 {attempt}/{max_attempts}）：'
                    f'{reason}；已停止重试')
            break
        if attempt < max_attempts:
            wait_seconds = 2 * attempt
            printer(f'  {operation_label}失败（尝试 {attempt}/{max_attempts}）：'
                    f'{reason}；{wait_seconds} 秒后重试')
            sleep_func(wait_seconds)
        else:
            printer(f'  {operation_label}失败（尝试 {attempt}/{max_attempts}）：'
                    f'{reason}；已停止重试')
    return None


def _api_failure_reason(data):
    data = data or {}
    message = data.get('msg') or data.get('message') or '接口返回 succeeded=false'
    code = data.get('code')
    return f'{message}（code={code}）' if code is not None else message


def _with_detail(summary, detail):
    if detail:
        return f'{summary}：{detail}'
    return summary
