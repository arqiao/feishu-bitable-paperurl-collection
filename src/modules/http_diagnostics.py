"""HTTP/API access failure classification helpers.

This module intentionally does not print, sleep, retry, or mutate state. It
only turns common response/exception shapes into a stable diagnosis object so
callers can decide how to report and retry in their own workflow.
"""

from dataclasses import dataclass

try:
    import requests
except ImportError:  # pragma: no cover - requests is a project dependency.
    requests = None


@dataclass(frozen=True)
class HttpFailureDiagnosis:
    category: str
    retryable: bool
    summary: str
    detail: str = ''
    action: str = ''
    status_code: int | None = None
    business_code: int | str | None = None


_BUSINESS_CODE_MAP = {
    1059: ('temporary', True, '接口临时异常'),
    '1059': ('temporary', True, '接口临时异常'),
    401: ('auth', False, '认证失败'),
    '401': ('auth', False, '认证失败'),
    403: ('permission', False, '访问权限不足'),
    '403': ('permission', False, '访问权限不足'),
    429: ('rate_limit', True, '接口限流'),
    '429': ('rate_limit', True, '接口限流'),
}


def classify_http_failure(error=None, response=None, status_code=None,
                          payload=None, message='', service='外部服务',
                          credential_hint=''):
    """Classify an HTTP/API failure into a stable, caller-friendly object.

    Args:
        error: Optional exception raised by a HTTP client.
        response: Optional response-like object with status_code/json/text.
        status_code: Optional explicit HTTP status code.
        payload: Optional decoded JSON body. `code` and `msg` are recognized.
        message: Optional caller-provided message.
        service: Human-readable service name used in user-facing text.
        credential_hint: Optional next action for auth failures.

    Returns:
        HttpFailureDiagnosis.
    """
    status = _resolve_status_code(error, response, status_code)
    body = _resolve_payload(response, payload)
    body_code = _get_body_code(body)
    body_msg = _get_body_message(body)
    combined_message = _join_nonempty(
        message,
        body_msg,
        _response_text(response),
        str(error) if error else '',
    )

    if body_code in _BUSINESS_CODE_MAP:
        category, retryable, label = _BUSINESS_CODE_MAP[body_code]
        return _diagnosis(
            category, retryable, service, label, combined_message,
            status, body_code, credential_hint)
    body_code_int = _to_int(body_code)
    if body_code_int is not None and body_code_int >= 500:
        return _diagnosis('server_error', True, service, '服务端异常',
                          combined_message, status, body_code,
                          credential_hint)

    if status == 401:
        return _diagnosis('auth', False, service, '认证失败', combined_message,
                          status, body_code, credential_hint)
    if status == 403:
        return _diagnosis('permission', False, service, '访问权限不足',
                          combined_message, status, body_code,
                          credential_hint)
    if status == 429:
        return _diagnosis('rate_limit', True, service, '接口限流',
                          combined_message, status, body_code,
                          credential_hint)
    if status and status >= 500:
        return _diagnosis('server_error', True, service, '服务端异常',
                          combined_message, status, body_code,
                          credential_hint)

    if _is_timeout(error):
        return _diagnosis('timeout', True, service, '请求超时',
                          combined_message, status, body_code,
                          credential_hint)
    if _is_network_error(error):
        return _diagnosis('network', True, service, '网络连接失败',
                          combined_message, status, body_code,
                          credential_hint)

    return _diagnosis('unknown', False, service, '访问失败',
                      combined_message, status, body_code, credential_hint)


def _diagnosis(category, retryable, service, label, message, status_code,
               business_code, credential_hint):
    detail_parts = []
    if status_code is not None:
        detail_parts.append(f'HTTP {status_code}')
    if business_code is not None:
        detail_parts.append(f'code={business_code}')
    if message:
        detail_parts.append(message)

    action = _default_action(category, credential_hint)
    return HttpFailureDiagnosis(
        category=category,
        retryable=retryable,
        summary=f'{service}{label}',
        detail='；'.join(detail_parts),
        action=action,
        status_code=status_code,
        business_code=business_code,
    )


def _default_action(category, credential_hint):
    if category == 'auth':
        return credential_hint or '请检查本机凭证配置是否存在、过期或填错。'
    if category == 'permission':
        return '请确认当前账号、应用或 Token 是否拥有访问目标资源的权限。'
    if category == 'rate_limit':
        return '建议稍后重试；如果持续出现，请降低请求频率。'
    if category in ('timeout', 'network'):
        return '请检查网络、代理、DNS 或目标服务连通性，可稍后重试。'
    if category in ('server_error', 'temporary'):
        return '通常可以稍后重试；如果连续失败，请记录时间和响应详情。'
    return '请查看响应详情，并确认接口、参数、凭证和服务状态。'


def _resolve_status_code(error, response, status_code):
    if status_code is not None:
        return _to_int(status_code)
    if response is not None and hasattr(response, 'status_code'):
        return _to_int(response.status_code)
    error_response = getattr(error, 'response', None)
    if error_response is not None and hasattr(error_response, 'status_code'):
        return _to_int(error_response.status_code)
    return None


def _resolve_payload(response, payload):
    if payload is not None:
        return payload
    if response is None or not hasattr(response, 'json'):
        return None
    try:
        return response.json()
    except Exception:
        return None


def _get_body_code(payload):
    if not isinstance(payload, dict):
        return None
    for key in ('code', 'errcode', 'error_code'):
        if key in payload:
            return payload.get(key)
    return None


def _get_body_message(payload):
    if not isinstance(payload, dict):
        return ''
    for key in ('msg', 'message', 'errmsg', 'error_description'):
        value = payload.get(key)
        if value:
            return str(value)
    return ''


def _response_text(response):
    if response is None:
        return ''
    text = getattr(response, 'text', '')
    return str(text) if text else ''


def _join_nonempty(*items):
    return '；'.join(str(item).strip() for item in items if str(item).strip())


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_timeout(error):
    if error is None or requests is None:
        return False
    return isinstance(error, requests.exceptions.Timeout)


def _is_network_error(error):
    if error is None or requests is None:
        return False
    network_types = (
        requests.exceptions.ConnectionError,
        requests.exceptions.ChunkedEncodingError,
    )
    return isinstance(error, network_types)
