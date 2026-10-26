"""Run the fake Telegram Ads API and the fake sales bot API over HTTP (for e2e tests and demos).

    python -m tests.mocks.server --tg-port 8790 --bot-port 8791 --claude-port 8792

Extra endpoints for tests:
  bot server  POST /__test/add_user {"key": "a", "referrer": "9001"}
              POST /__test/start {"key": "a", "code": "9934521877"}   (a new account starts the bot with it)
              POST /__test/stores_unknown {"value": true}           (record users of unknown start codes)
  Telegram    POST /__test/review {"key": "a", "ad_id": null, "decision": "approve"|"decline", "reason": ""}
              POST /__test/set_ad {"key": "a", "ad_id": 11, "fields": {"remaining_budget": 1.0}}
              POST /__test/set_balance {"key": "a", "amount": 5}
"""

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.mocks.bot_mock import RAW, BotMock
from tests.mocks.tgads_mock import TgAdsMock


def _parse_multipart(body: bytes, ctype: str) -> tuple[dict, dict]:
    boundary = ctype.split("boundary=")[1].encode()
    params, files = {}, {}
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b"filename=" in head:
            files[name] = data.rstrip(b"\r\n")
        else:
            params[name] = data.rstrip(b"\r\n").decode()
    return params, files


def make_tg_handler(mock: TgAdsMock) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path.startswith("/__test/"):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                key = body.get("key", "a")
                if self.path == "/__test/review":
                    res: object = mock.review(
                        key, body.get("ad_id"), body.get("decision", "approve"), body.get("reason", "")
                    )
                elif self.path == "/__test/set_ad":
                    mock.set_ad(key, int(body["ad_id"]), **body.get("fields", {}))
                    res = True
                elif self.path == "/__test/set_balance":
                    mock.set_balance(key, float(body["amount"]))
                    res = True
                else:
                    res = False
                self._send(200, {"ok": True, "result": res})
                return
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            length = self.headers.get("Content-Length")
            if length is None:
                self._send(411, {"ok": False, "error": "LENGTH_REQUIRED"})
                return
            body = self.rfile.read(int(length))
            ctype = self.headers.get("Content-Type", "")
            if ctype.startswith("multipart/"):
                params, files = _parse_multipart(body, ctype)
            else:
                params, files = json.loads(body or b"{}"), {}
            res = mock.handle(token, self.path.strip("/"), params, files, self.headers.get("Idempotency-Key"))
            self._send(200, res)

        def _send(self, code: int, obj: dict) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a: object) -> None:
            pass

    return H


def make_bot_handler(mock: BotMock) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if self.path == "/__test/start":
                uid = mock.start(body.get("key", "a"), str(body.get("code", "")))
                self._send(200, {"ok": True, "user_id": uid})
                return
            if self.path == "/__test/stores_unknown":
                mock.stores_unknown_start = bool(body.get("value", True))
                self._send(200, {"ok": True})
                return
            if self.path == "/__test/add_user":
                uid = mock.add_user(
                    body.get("key", "a"), str(body.get("referrer", "9001")), bool(body.get("buy", True))
                )
                self._send(200, {"ok": True, "user_id": uid})
                return
            code, res = mock.handle(self.headers.get("Token", ""), method, self.path, body)
            if RAW in res:
                b = res[RAW].encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            self._send(code, res)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def _send(self, code: int, obj: dict) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a: object) -> None:
            pass

    return H


CLAUDE_KEY = "sk-ant-test"


def claude_reply(user: str) -> str:
    """Canned Claude answers: JSON for the text analysis prompt, Markdown otherwise."""
    if "سه بازنویسی" in user:
        data = {
            "score": 62,
            "summary": "پیشنهاد روشن ولی بدون نام برند.",
            "strengths": ["قیمت روشن"],
            "issues": [{"issue": "نام برند نیست", "severity": "high", "fix": "نام برند را اول بیاورید"}],
            "rewrites": [
                {"text": "سایکونت | تست رایگان VPN 👇", "angle": "تست رایگان"},
                {"text": "x" * 170, "angle": "خیلی بلند"},
                {"text": "وصل بمون با سایکونت 👇", "angle": "پایداری"},
            ],
        }
        return "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"
    if "بازنویسی منطبق با قوانین" in user:
        data = {
            "text": "سایکونت | اینترنت پایدار و پرسرعت با تست رایگان؛ در ربات امتحان کنید 👇",
            "notes": ["فوریت ساختگی و ایموجی اضافه حذف شد"],
        }
        return json.dumps(data, ensure_ascii=False)
    return "## تحلیل\n- آگهی‌های **کانال** ارزان‌ترند.\n- بودجه را به آگهی‌های خوب منتقل کنید.\n\n| آگهی | پیشنهاد |\n|---|---|\n| 1 | شارژ |\n\nOK"


def make_claude_handler() -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.headers.get("x-api-key") != CLAUDE_KEY:
                self._send(401, {"error": {"message": "invalid x-api-key"}})
                return
            user = body["messages"][0]["content"]
            self._send(200, {"content": [{"type": "text", "text": claude_reply(user)}]})

        def _send(self, code: int, obj: dict) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a: object) -> None:
            pass

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg-port", type=int, default=8790)
    ap.add_argument("--bot-port", type=int, default=8791)
    ap.add_argument("--claude-port", type=int, default=8792)
    args = ap.parse_args()
    tg = TgAdsMock(inactive_seconds=float(os.environ.get("MOCK_INACTIVE_SECONDS", "3")))
    bot = BotMock()
    servers = [
        ThreadingHTTPServer(("127.0.0.1", args.tg_port), make_tg_handler(tg)),
        ThreadingHTTPServer(("127.0.0.1", args.bot_port), make_bot_handler(bot)),
        ThreadingHTTPServer(("127.0.0.1", args.claude_port), make_claude_handler()),
    ]
    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(
        f"mock Telegram Ads on :{args.tg_port} (tokens mocktoken-a, mocktoken-b); "
        f"mock bot on :{args.bot_port} (keys botkey-a, botkey-b); mock Claude on :{args.claude_port} ({CLAUDE_KEY})",
        flush=True,
    )
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
