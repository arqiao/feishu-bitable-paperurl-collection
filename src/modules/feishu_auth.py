"""
飞书授权脚本
用于获取用户授权，获取 access_token 和 refresh_token
"""

import os
import requests
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import webbrowser
import threading

try:
    from secrets_loader import load as _secrets_load
except ImportError:
    import sys
    sys.path.insert(0, "/Volumes/DATADRIVE/workspace/sys")
    from secrets_loader import load as _secrets_load

# 默认凭证文件路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_AUTH_KEY = "auth_feishuMSG-xls"


class AuthCallbackHandler(BaseHTTPRequestHandler):
    """授权回调处理器"""
    auth_code = None

    def do_GET(self):
        """处理 GET 请求"""
        print(f"[DEBUG] 收到请求: {self.path}")
        # 解析 URL
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)

        # 获取授权码
        if 'code' in query_params:
            AuthCallbackHandler.auth_code = query_params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write("""
                <html>
                <head><title>授权成功</title></head>
                <body>
                    <h1>授权成功！</h1>
                    <p>您可以关闭此页面，返回终端继续操作。</p>
                </body>
                </html>
            """.encode('utf-8'))
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write("""
                <html>
                <head><title>授权失败</title></head>
                <body>
                    <h1>授权失败！</h1>
                    <p>未获取到授权码，请重试。</p>
                </body>
                </html>
            """.encode('utf-8'))

    def log_message(self, format, *args):
        """输出日志"""
        print(f"[SERVER LOG] {format % args}")


def load_credentials(path: str = None):
    """加载凭证 — 从 ~/.config/secrets/ 集中存储"""
    if path:
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}

    secrets_dir = Path.home() / ".config" / "secrets"
    creds = {}
    for name in ("global", "gkeys", "gfeishu", "gtokens"):
        item_path = secrets_dir / f"{name}.yaml"
        if not item_path.exists():
            continue
        with open(item_path, 'r', encoding='utf-8') as f:
            _deep_merge(creds, yaml.safe_load(f) or {})
    return creds


import yaml


def _deep_merge(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _format_scopes(scopes):
    if isinstance(scopes, str):
        return scopes
    if isinstance(scopes, (list, tuple)):
        return ' '.join(scopes)
    return ''

def save_credentials(creds, path: str = None):
    """保存本项目 token 到集中 secrets 目录。"""
    path = Path(path) if path else Path.home() / ".config" / "secrets" / "gfeishu.yaml"
    current = {}
    if path.exists():
        current = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    current[_PROJECT_AUTH_KEY] = creds[_PROJECT_AUTH_KEY]
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(current, f, allow_unicode=True, default_flow_style=False)


def get_authorization_url(creds):
    """生成授权 URL"""
    app_id = creds['feishu']['app_id']
    redirect_uri = creds['feishu']['redirect_uri']
    scopes = _format_scopes(
        creds.get(_PROJECT_AUTH_KEY, {}).get('scopes', creds['feishu'].get('scopes', []))
    )

    auth_url = (
        f"https://open.feishu.cn/open-apis/authen/v1/authorize?"
        f"app_id={app_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={scopes}&"
        f"state=STATE"
    )
    return auth_url


def exchange_code_for_token(creds, auth_code: str):
    """使用授权码换取 token"""
    url = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
    data = {
        "grant_type": "authorization_code",
        "code": auth_code
    }
    headers = {
        "Authorization": f"Bearer {get_app_access_token(creds)}",
        "Content-Type": "application/json; charset=utf-8"
    }

    response = requests.post(url, json=data, headers=headers)
    result = response.json()

    if result.get('code') == 0:
        return result['data']
    else:
        print(f"换取 token 失败: {result.get('msg')}")
        return None


def get_app_access_token(creds):
    """获取应用 access token（用于换取用户 token）"""
    url = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
    data = {
        "app_id": creds['feishu']['app_id'],
        "app_secret": creds['feishu']['app_secret']
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8"
    }

    response = requests.post(url, json=data, headers=headers)
    result = response.json()

    if result.get('code') == 0:
        return result['app_access_token']
    else:
        print(f"获取应用 token 失败: {result.get('msg')}")
        return None


def start_auth_server(port: int = 8080):
    """启动授权回调服务器"""
    server = HTTPServer(('localhost', port), AuthCallbackHandler)
    server.timeout = 1
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    return server


def main():
    """主函数"""
    print("=" * 60)
    print("飞书应用授权工具")
    print("=" * 60)

    # 加载凭证配置
    creds = load_credentials()

    # 检查配置
    if creds['feishu']['app_id'] == 'YOUR_APP_ID':
        print("\n错误：请先在 ~/.config/secrets/gfeishu.yaml 中配置 App ID 和 App Secret！")
        return

    # 生成授权 URL
    auth_url = get_authorization_url(creds)
    print(f"\n步骤 1: 请在浏览器中打开以下链接进行授权：")
    print(f"\n{auth_url}\n")

    # 启动回调服务器
    print("步骤 2: 启动本地回调服务器...")
    server = start_auth_server(8080)
    print("回调服务器已启动，等待授权回调...\n")

    # 自动打开浏览器
    try:
        webbrowser.open(auth_url)
        print("已自动打开浏览器，请完成授权操作。\n")
    except:
        print("无法自动打开浏览器，请手动复制上面的链接到浏览器中打开。\n")

    # 等待授权码
    print("等待授权...")
    timeout = 300  # 5 分钟超时
    start_time = time.time()

    while AuthCallbackHandler.auth_code is None:
        if time.time() - start_time > timeout:
            print("\n授权超时，请重新运行脚本。")
            return
        time.sleep(1)

    auth_code = AuthCallbackHandler.auth_code
    print(f"\n步骤 3: 已获取授权码")

    # 换取 token
    print("步骤 4: 正在换取 access token...")
    token_data = exchange_code_for_token(creds, auth_code)

    if token_data:
        # 保存 token
        creds[_PROJECT_AUTH_KEY]['user_access_token'] = token_data['access_token']
        creds[_PROJECT_AUTH_KEY]['user_refresh_token'] = token_data['refresh_token']
        creds[_PROJECT_AUTH_KEY]['user_token_expire_time'] = int(time.time()) + token_data['expires_in']
        save_credentials(creds)

        print("\n✓ 授权成功！Token 已保存到 ~/.config/secrets/gfeishu.yaml")
        print(f"✓ Access Token: {token_data['access_token'][:20]}...")
        print(f"✓ Token 有效期: {token_data['expires_in']} 秒")
        print("\n授权完成，现在可以运行主程序了。")
    else:
        print("\n✗ 授权失败，请检查配置后重试。")


if __name__ == "__main__":
    main()
