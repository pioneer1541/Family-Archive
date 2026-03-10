#!/usr/bin/env python3
import argparse
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_PORT = 18080
DEFAULT_TIMEOUT_SECONDS = 300


class _OAuthCallbackState:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.code = ""
        self.error = ""
        self.state = ""


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    state_store: _OAuthCallbackState | None = None

    def log_message(self, _format: str, *_args) -> None:
        # Silence default request logs; keep CLI output clean.
        return

    def _write_html(self, status_code: int, title: str, body: str) -> None:
        payload = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
</head>
<body style="font-family: sans-serif; padding: 24px;">
  <h2>{title}</h2>
  <p>{body}</p>
  <p>You can close this window now.</p>
</body>
</html>
"""
        encoded = payload.encode("utf-8", errors="ignore")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self._write_html(404, "Not Found", "Expected callback path: /callback")
            return

        store = self.__class__.state_store
        if store is None:
            self._write_html(500, "Internal Error", "Authorization state store unavailable.")
            return

        query = parse_qs(parsed.query)
        store.code = str((query.get("code") or [""])[0] or "").strip()
        store.error = str((query.get("error") or [""])[0] or "").strip()
        store.state = str((query.get("state") or [""])[0] or "").strip()
        store.event.set()

        if store.error:
            self._write_html(400, "Authorization Failed", f"Google returned error: {store.error}")
            return
        if not store.code:
            self._write_html(400, "Authorization Failed", "Missing authorization code in callback.")
            return
        self._write_html(200, "Authorization Success", "Gmail authorization completed successfully.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local Gmail OAuth authorization and save token.json.")
    parser.add_argument(
        "--credentials",
        required=True,
        help="Path to Google OAuth client credentials.json downloaded from Google Cloud Console.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Local callback server port (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--output",
        default="token.json",
        help='Output token file path (default: "token.json").',
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Authorization timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    return parser.parse_args()


def _ensure_readable_file(path_text: str, arg_name: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"{arg_name} not found: {path}")
    if not path.is_file():
        raise ValueError(f"{arg_name} is not a file: {path}")
    return path


def _ensure_port_valid(port: int) -> int:
    if not (1 <= int(port) <= 65535):
        raise ValueError("--port must be between 1 and 65535.")
    return int(port)


def _assert_port_available(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("localhost", int(port)))
    except OSError as exc:
        raise OSError(f"Port {port} is unavailable: {exc}") from exc


def _load_client_id(credentials_path: Path) -> str:
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for key in ("installed", "web"):
        block = data.get(key)
        if isinstance(block, dict):
            client_id = str(block.get("client_id") or "").strip()
            if client_id:
                return client_id
    return ""


def main() -> int:
    try:
        args = _parse_args()
        credentials_path = _ensure_readable_file(str(args.credentials), "--credentials")
        output_path = Path(str(args.output)).expanduser().resolve()
        port = _ensure_port_valid(int(args.port))
        timeout_seconds = max(10, int(args.timeout))
        _assert_port_available(port)
    except Exception as exc:
        print(f"[ERROR] 参数校验失败: {exc}", file=sys.stderr)
        return 2

    redirect_uri = f"http://localhost:{port}/callback"
    print("[1/5] 初始化 Gmail OAuth 流程...")
    print(f"      credentials: {credentials_path}")
    print(f"      redirect_uri: {redirect_uri}")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception:
        print(
            "[ERROR] 缺少依赖: google-auth-oauthlib。请先安装 backend/requirements.txt 中的依赖。",
            file=sys.stderr,
        )
        return 1

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        flow.redirect_uri = redirect_uri
        auth_url, expected_state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
    except Exception as exc:
        print(f"[ERROR] 初始化 OAuth 失败: {exc}", file=sys.stderr)
        return 1

    callback_state = _OAuthCallbackState()
    _OAuthCallbackHandler.state_store = callback_state
    server = HTTPServer(("localhost", port), _OAuthCallbackHandler)
    server.timeout = 1.0

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    print(f"[2/5] 本地回调服务器已启动: http://localhost:{port}/callback")
    server_thread.start()

    print("[3/5] 请在浏览器中打开以下授权链接:")
    print(auth_url)
    print(f"      等待 Google 回调（超时: {timeout_seconds} 秒）...")

    started = time.monotonic()
    while (time.monotonic() - started) < timeout_seconds:
        if callback_state.event.wait(timeout=1.0):
            break

    try:
        server.shutdown()
    finally:
        server.server_close()
        server_thread.join(timeout=3.0)

    if not callback_state.event.is_set():
        print("[ERROR] 授权超时，未收到 Google 回调。", file=sys.stderr)
        return 1
    if callback_state.error:
        print(f"[ERROR] Google 授权失败: {callback_state.error}", file=sys.stderr)
        return 1
    if not callback_state.code:
        print("[ERROR] 回调中缺少 code 参数。", file=sys.stderr)
        return 1
    if callback_state.state != expected_state:
        print("[ERROR] state 不匹配，可能存在 CSRF 风险。", file=sys.stderr)
        return 1

    print("[4/5] 正在交换 token...")
    try:
        flow.fetch_token(code=callback_state.code)
        creds = flow.credentials
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:
        print(f"[ERROR] token 获取或保存失败: {exc}", file=sys.stderr)
        return 1

    client_id = _load_client_id(credentials_path)
    print("[5/5] 授权完成，token 已保存。")
    print(f"      token: {output_path}")
    if client_id:
        print(f"      client_id: {client_id}")
    print("")
    print("下一步:")
    print(f"1. 将 credentials.json 放到服务可读取路径（例如: /app/secrets/gmail/credentials.json）")
    print(f"2. 将 token 文件放到服务可读取路径（例如: /app/secrets/gmail/token.json）")
    print("3. 重启 API/Worker 服务后，在系统健康检查中确认 Gmail 状态为 ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
