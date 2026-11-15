#!/usr/bin/env python3
"""Cài đặt listing-watcher: hỏi token, tự tìm chat id, ghi file .env, chạy thử.

Token chỉ được lưu trong file .env trên máy bạn.
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import watcher

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
TEST_PR = "BitGo/BitGoJS#9357"


def http_json(url: str, headers: dict | None = None, timeout: int = 40):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "listing-watcher"})
    with watcher.open_url(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def diagnose() -> None:
    """In ra kết nối nào chạy, kết nối nào hỏng, để dễ tìm nguyên nhân."""
    print("\n--- Kiểm tra mạng ---")
    proxies = urllib.request.getproxies()
    shown = ", ".join(f"{k}={v}" for k, v in sorted(proxies.items())
                      if k in ("http", "https", "all"))
    print("Proxy hệ thống:", shown or "không có")
    for host in ("api.telegram.org", "api.github.com"):
        for label, family in (("IPv4", socket.AF_INET), ("IPv6", socket.AF_INET6)):
            try:
                info = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)[0]
            except OSError:
                print(f"{host} {label}: không có địa chỉ")
                continue
            try:
                with socket.socket(info[0], info[1]) as raw:
                    raw.settimeout(10)
                    raw.connect(info[4])
                    ctx = ssl.create_default_context()
                    with ctx.wrap_socket(raw, server_hostname=host):
                        print(f"{host} {label}: OK")
            except Exception as err:
                print(f"{host} {label}: LỖI ({type(err).__name__}: {err})")
    print("--- Hết phần kiểm tra. Chụp màn hình đoạn này nếu vẫn lỗi. ---\n")


def telegram(token: str, method: str, query: str = "", fetch=http_json):
    return fetch(f"https://api.telegram.org/bot{token}/{method}{query}")


def ask_bot_token(ask=input, fetch=http_json, on_network_error=None) -> tuple[str, str]:
    """Hỏi đến khi token hợp lệ. Trả về (token, tên người dùng của bot)."""
    token = ""
    diagnosed = False
    while True:
        if not token:
            token = ask("Dán token của bot Telegram rồi nhấn Enter: ").strip()
            if not token:
                continue
        try:
            me = telegram(token, "getMe", fetch=fetch)
            return token, me["result"]["username"]
        except urllib.error.HTTPError as err:
            if err.code in (401, 404):
                print("Token không đúng. Chép lại nguyên dòng token từ BotFather.")
                token = ""
            else:
                print(f"Telegram báo lỗi {err.code}.")
                ask("Nhấn Enter để thử lại (không cần dán lại token): ")
        except Exception as err:
            print(f"Không kết nối được Telegram ({err}).")
            if on_network_error and not diagnosed:
                on_network_error()
                diagnosed = True
            ask("Nhấn Enter để thử lại (không cần dán lại token): ")


def chat_id_from(updates: dict) -> str | None:
    for update in reversed(updates.get("result", [])):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat and "id" in chat:
                return str(chat["id"])
    return None


def wait_for_chat_id(token: str, fetch=http_json, attempts: int = 24,
                     pause: float = 1.0) -> str | None:
    """Chờ người dùng nhắn cho bot (tối đa khoảng 10 phút)."""
    for _ in range(attempts):
        try:
            chat_id = chat_id_from(telegram(token, "getUpdates", "?timeout=25", fetch=fetch))
            if chat_id:
                return chat_id
        except Exception as err:
            print(f"  (chưa đọc được tin nhắn: {err})")
        time.sleep(pause)
    return None


def ask_github_token(ask=input, fetch=http_json) -> str:
    while True:
        token = ask("Dán GitHub token (hoặc nhấn Enter để bỏ qua): ").strip()
        if not token:
            return ""
        try:
            fetch("https://api.github.com/rate_limit", headers={
                "Authorization": f"Bearer {token}", "User-Agent": "listing-watcher"})
            return token
        except urllib.error.HTTPError as err:
            if err.code == 401:
                print("GitHub không nhận token này. Dán lại, hoặc nhấn Enter để bỏ qua.")
            else:
                print(f"GitHub báo lỗi {err.code}. Bỏ qua bước này.")
                return ""
        except Exception as err:
            print(f"Không kết nối được GitHub ({err}). Bỏ qua bước này.")
            return ""


def write_env(path: Path, bot_token: str, chat_id: str, github_token: str) -> None:
    path.write_text(
        f"TELEGRAM_BOT_TOKEN={bot_token}\n"
        f"TELEGRAM_CHAT_ID={chat_id}\n"
        f"GITHUB_TOKEN={github_token}\n", encoding="utf-8")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    print("=== Cài đặt listing-watcher ===\n")
    if ENV_PATH.exists():
        if input("Đã có file .env. Cài lại từ đầu? (c/k): ").strip().lower() != "c":
            print("Giữ nguyên cài đặt cũ.")
            return 0

    print("Bước 1/3: bot Telegram")
    print("  Trong Telegram: nhắn @BotFather, gõ /newbot, đặt tên, rồi chép token nó gửi.\n")
    bot_token, username = ask_bot_token(on_network_error=diagnose)
    print(f"  Token đúng. Bot của bạn: @{username}\n")

    print("Bước 2/3: kết nối với tài khoản Telegram của bạn")
    print(f"  Mở https://t.me/{username} và nhắn một tin bất kỳ cho bot (ví dụ: hi).")
    print("  Đang chờ tin nhắn...")
    chat_id = wait_for_chat_id(bot_token)
    if not chat_id:
        print("  Chưa nhận được tin nhắn nào. Nhắn cho bot rồi chạy lại setup.")
        return 1
    print("  Đã nhận.\n")

    print("Bước 3/3: GitHub token (không bắt buộc)")
    print("  Có token thì bot kiểm tra mỗi phút, không có thì 3 phút một lần.")
    github_token = ask_github_token()

    write_env(ENV_PATH, bot_token, chat_id, github_token)
    print("\nĐã lưu cài đặt vào file .env.\n")

    print("Gửi tin thử về Telegram...")
    watcher.main(["--test-telegram"])
    print(f"\nChạy thử trên PR cashcat thật ({TEST_PR})...")
    try:
        watcher.main(["--replay", TEST_PR])
    except Exception as err:
        print(f"Chạy thử GitHub thất bại: {err}")
        return 1
    print("\nXong. Nếu Telegram nhận được 2 tin thì mọi thứ đã ổn.")
    print("Từ giờ, bấm đúp start.bat để chạy bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
