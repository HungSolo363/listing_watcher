#!/usr/bin/env python3
"""listing-watcher

Theo dõi các repo GitHub công khai và báo về Telegram khi có mã token mới
xuất hiện trong pull request hoặc commit.

Chỉ dùng thư viện chuẩn của Python (cần Python 3.11 trở lên).

Cách chạy:
    python watcher.py                      # chạy liên tục
    python watcher.py --once               # chạy một vòng rồi thoát
    python watcher.py --replay BitGo/BitGoJS#9357   # xem bot sẽ báo gì với một PR cũ
    python watcher.py --diff-file x.diff   # thử bộ lọc trên một file diff có sẵn
    python watcher.py --test-telegram      # gửi thử một tin nhắn Telegram
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import socket
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.github.com"
DEFAULT_IDENTIFIER_REGEX = r"""['"]((?:ofc)?[a-z0-9]+:[a-z0-9$._-]+)['"]"""
DISCLAIMER = (
    "Lưu ý: đây là metadata kỹ thuật công khai, KHÔNG phải bằng chứng niêm yết."
)


# --------------------------------------------------------------------------
# Mạng
# --------------------------------------------------------------------------
_ORIG_GETADDRINFO = socket.getaddrinfo
_ipv4_only = False


def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
    return _ORIG_GETADDRINFO(host, port, socket.AF_INET, *args, **kwargs)


def open_url(req, timeout: int = 30):
    """Như urlopen, nhưng nếu kết nối bị ngắt thì thử lại chỉ bằng IPv4.

    Một số mạng có đường IPv6 hỏng: trình duyệt tự né được, Python thì không.
    """
    global _ipv4_only
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except OSError as first_error:
        if _ipv4_only:
            raise
        socket.getaddrinfo = _ipv4_getaddrinfo
        _ipv4_only = True
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            print("[mạng] kết nối thường bị ngắt, đã chuyển sang IPv4.")
            raise
        except OSError:
            socket.getaddrinfo = _ORIG_GETADDRINFO
            _ipv4_only = False
            raise first_error from None
        print("[mạng] kết nối thường bị ngắt, đã chuyển sang IPv4.")
        return resp


# --------------------------------------------------------------------------
# Cấu hình và trạng thái
# --------------------------------------------------------------------------
def load_env_file(path: Path) -> None:
    """Đọc file .env (KEY=VALUE) vào biến môi trường, không ghi đè biến đã có."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: Path) -> dict:
    with path.open("rb") as fh:
        cfg = tomllib.load(fh)
    if not cfg.get("repos"):
        raise SystemExit(f"{path}: chưa khai báo repo nào trong [[repos]]")
    for repo in cfg["repos"]:
        repo.setdefault("path_globs", ["*"])
        repo.setdefault("identifier_regex", DEFAULT_IDENTIFIER_REGEX)
        repo.setdefault("watch_chains", [])
        repo.setdefault("alert_other_chains", True)
        repo["_regex"] = re.compile(repo["identifier_regex"])
    cfg.setdefault("poll_seconds", 60)
    return cfg


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def repo_state(state: dict, name: str) -> dict:
    rs = state.setdefault("repos", {}).setdefault(name, {})
    rs.setdefault("seen", {})        # identifier -> thông tin lần đầu thấy
    rs.setdefault("pr_heads", {})    # số PR -> head sha đã xử lý
    rs.setdefault("commits", [])     # các commit sha đã xử lý
    rs.setdefault("baselined", False)
    return rs


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------
class RateLimited(Exception):
    def __init__(self, reset_epoch: float):
        super().__init__("GitHub rate limit")
        self.reset_epoch = reset_epoch


class GitHub:
    """Client tối giản cho GitHub REST API, có hỗ trợ ETag."""

    def __init__(self, token: str | None, etags: dict):
        self.token = token
        self.etags = etags

    def get(self, path: str, accept: str = "application/vnd.github+json",
            use_etag: bool = False) -> bytes | None:
        """Trả về nội dung, hoặc None nếu máy chủ báo 304 (không có gì thay đổi)."""
        url = path if path.startswith("http") else API + path
        headers = {
            "Accept": accept,
            "User-Agent": "listing-watcher",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if use_etag and url in self.etags:
            headers["If-None-Match"] = self.etags[url]
        req = urllib.request.Request(url, headers=headers)
        try:
            with open_url(req, timeout=30) as resp:
                if use_etag and resp.headers.get("ETag"):
                    self.etags[url] = resp.headers["ETag"]
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 304:
                return None
            remaining = err.headers.get("X-RateLimit-Remaining")
            if err.code in (403, 429) and (remaining == "0" or err.headers.get("Retry-After")):
                retry = err.headers.get("Retry-After")
                reset = (time.time() + float(retry)) if retry else float(
                    err.headers.get("X-RateLimit-Reset", time.time() + 60))
                raise RateLimited(reset) from None
            raise

    def get_json(self, path: str, use_etag: bool = False):
        body = self.get(path, use_etag=use_etag)
        return None if body is None else json.loads(body)

    def get_diff(self, path: str) -> str:
        body = self.get(path, accept="application/vnd.github.diff")
        return (body or b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Phân tích diff
# --------------------------------------------------------------------------
def added_lines(diff: str):
    """Sinh ra (đường dẫn file, dòng được thêm) từ một unified diff."""
    path = None
    prev = ""
    for line in diff.splitlines():
        if line.startswith("+++ ") and prev.startswith("--- "):
            target = line[4:].strip()
            if target == "/dev/null":
                path = None
            else:
                path = target[2:] if target.startswith("b/") else target
        elif line.startswith("+") and path is not None:
            yield path, line[1:]
        prev = line


def normalize(identifier: str) -> str:
    """'ofchoodeth:cashcat' và 'hoodeth:cashcat' là cùng một token."""
    return re.sub(r"^ofc(?=[a-z0-9]+:)", "", identifier)


def extract(diff: str, repo_cfg: dict) -> dict[str, set[str]]:
    """Trả về {identifier: {các file chứa nó}} cho những dòng được thêm."""
    hits: dict[str, set[str]] = {}
    globs = repo_cfg["path_globs"]
    regex = repo_cfg["_regex"]
    for path, text in added_lines(diff):
        if not any(fnmatch.fnmatch(path, g) for g in globs):
            continue
        for match in regex.finditer(text):
            hits.setdefault(normalize(match.group(1)), set()).add(path)
    return hits


def chain_of(identifier: str) -> str:
    return identifier.split(":", 1)[0]


# --------------------------------------------------------------------------
# Thông báo
# --------------------------------------------------------------------------
class Notifier:
    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.sent: list[str] = []   # để kiểm thử
        self.retry_pause = 2.0

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        """Gửi cảnh báo. Trả về False nếu Telegram không nhận (để lần sau gửi lại)."""
        self.sent.append(text)
        print("\n" + "=" * 60 + "\n" + text + "\n" + "=" * 60, flush=True)
        if not self.enabled:
            return True
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        error = None
        for attempt in range(3):
            try:
                open_url(req, timeout=20).read()
                return True
            except Exception as err:
                error = err
                time.sleep(self.retry_pause * (attempt + 1))
        print(f"[cảnh báo] gửi Telegram thất bại, sẽ thử lại ở vòng sau: {error}",
              file=sys.stderr)
        return False


def build_alert(repo_cfg: dict, new_hits: dict[str, set[str]], source: dict) -> str | None:
    watch = set(repo_cfg["watch_chains"])
    high = sorted(i for i in new_hits if chain_of(i) in watch)
    other = sorted(i for i in new_hits if chain_of(i) not in watch)
    if not high and not (other and repo_cfg["alert_other_chains"]):
        return None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = []
    if high:
        lines.append("🔴 ƯU TIÊN CAO · " + ", ".join(high))
    else:
        lines.append("⚪ Token mới · " + ", ".join(other[:10]))
    lines.append(f"Nguồn: {repo_cfg['name']} · {source['kind']} {source['ref']}")
    if source.get("title"):
        lines.append(f"Tiêu đề: {source['title']}")
    lines.append(f"Phát hiện: {now}")
    if high and other and repo_cfg["alert_other_chains"]:
        extra = f" (+{len(other) - 15} nữa)" if len(other) > 15 else ""
        lines.append("Token khác trong cùng thay đổi: " + ", ".join(other[:15]) + extra)
    files = sorted({f for i in (high or other) for f in new_hits[i]})
    lines.append("File: " + ", ".join(files[:6]))
    lines.append(source["url"])
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Vòng kiểm tra
# --------------------------------------------------------------------------
def handle_diff(diff: str, repo_cfg: dict, rs: dict, source: dict,
                notifier: Notifier, silent: bool) -> tuple[dict[str, set[str]], bool]:
    """Trả về (token mới, đã xử lý xong chưa). Chưa xong thì vòng sau làm lại."""
    hits = extract(diff, repo_cfg)
    new_hits = {i: f for i, f in hits.items() if i not in rs["seen"]}
    delivered = True
    if new_hits and not silent:
        alert = build_alert(repo_cfg, new_hits, source)
        if alert:
            delivered = notifier.send(alert)
    if delivered:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for ident in new_hits:
            rs["seen"][ident] = {"first_seen": stamp, "url": source["url"]}
    return new_hits, delivered


def check_repo(gh: GitHub, repo_cfg: dict, state: dict, notifier: Notifier) -> None:
    name = repo_cfg["name"]
    rs = repo_state(state, name)
    baseline = not rs["baselined"]
    pending_high: dict[str, str] = {}
    watch = set(repo_cfg["watch_chains"])

    # 1) Pull request đang mở (kể cả draft): tín hiệu sớm nhất
    prs = gh.get_json(
        f"/repos/{name}/pulls?state=open&sort=updated&direction=desc&per_page=50",
        use_etag=True)
    for pr in prs or []:
        num, sha = str(pr["number"]), pr["head"]["sha"]
        if rs["pr_heads"].get(num) == sha:
            continue
        diff = gh.get_diff(f"/repos/{name}/pulls/{num}")
        source = {"kind": "PR", "ref": f"#{num}", "title": pr.get("title", ""),
                  "url": pr["html_url"]}
        new_hits, done = handle_diff(diff, repo_cfg, rs, source, notifier, silent=baseline)
        if done:
            rs["pr_heads"][num] = sha
        if baseline:
            for ident in new_hits:
                if chain_of(ident) in watch:
                    pending_high[ident] = pr["html_url"]

    # 2) Commit trên nhánh chính: bắt các thay đổi không đi qua PR đang mở
    commits = gh.get_json(f"/repos/{name}/commits?per_page=20", use_etag=True)
    for commit in reversed(commits or []):
        sha = commit["sha"]
        if sha in rs["commits"]:
            continue
        if not baseline:
            diff = gh.get_diff(f"/repos/{name}/commits/{sha}")
            title = commit["commit"]["message"].splitlines()[0]
            source = {"kind": "commit", "ref": sha[:7], "title": title,
                      "url": commit["html_url"]}
            _, done = handle_diff(diff, repo_cfg, rs, source, notifier, silent=False)
            if not done:
                break   # giữ nguyên thứ tự: vòng sau thử lại từ commit này
        rs["commits"].append(sha)
    rs["commits"] = rs["commits"][-500:]

    if baseline:
        lines = [f"✅ Bắt đầu theo dõi {name}: ghi nhận {len(rs['seen'])} mã token "
                 f"trong {len(rs['pr_heads'])} PR đang mở làm mốc."]
        if pending_high:
            lines.append("Đang chờ trong PR mở (chain ưu tiên):")
            lines += [f"• {ident}  {url}" for ident, url in sorted(pending_high.items())]
        lines.append("Từ giờ, token mới sẽ được báo về đây.")
        if notifier.send("\n".join(lines)):
            rs["baselined"] = True


def run_cycle(gh: GitHub, cfg: dict, state: dict, notifier: Notifier) -> int:
    """Chạy một vòng cho mọi repo. Trả về số repo bị lỗi."""
    errors = 0
    for repo_cfg in cfg["repos"]:
        try:
            check_repo(gh, repo_cfg, state, notifier)
        except RateLimited:
            raise
        except Exception as err:
            errors += 1
            print(f"[{repo_cfg['name']}] lỗi: {err}", file=sys.stderr)
    return errors


# --------------------------------------------------------------------------
# Lệnh phụ
# --------------------------------------------------------------------------
def find_repo_cfg(cfg: dict, name: str | None) -> dict:
    if name is None:
        return cfg["repos"][0]
    for repo in cfg["repos"]:
        if repo["name"].lower() == name.lower():
            return repo
    raise SystemExit(f"Repo {name} chưa có trong config.toml")


def cmd_diff_file(cfg: dict, args, notifier: Notifier) -> None:
    repo_cfg = find_repo_cfg(cfg, args.repo)
    diff = Path(args.diff_file).read_text(encoding="utf-8", errors="replace")
    hits = extract(diff, repo_cfg)
    source = {"kind": "diff", "ref": Path(args.diff_file).name, "title": "",
              "url": "(file cục bộ)"}
    alert = build_alert(repo_cfg, hits, source)
    notifier.send(alert or "Không có mã token nào khớp bộ lọc.")


def cmd_replay(gh: GitHub, cfg: dict, args, notifier: Notifier) -> None:
    match = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", args.replay)
    if not match:
        raise SystemExit("Cú pháp: --replay OWNER/REPO#SỐ_PR")
    name, num = match.groups()
    repo_cfg = find_repo_cfg(cfg, name)
    pr = gh.get_json(f"/repos/{name}/pulls/{num}")
    diff = gh.get_diff(f"/repos/{name}/pulls/{num}")
    source = {"kind": "PR", "ref": f"#{num}", "title": pr.get("title", ""),
              "url": pr["html_url"]}
    alert = build_alert(repo_cfg, extract(diff, repo_cfg), source)
    notifier.send(alert or "Không có mã token nào khớp bộ lọc.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Theo dõi token mới trên GitHub")
    parser.add_argument("--config", default=str(HERE / "config.toml"))
    parser.add_argument("--state", default=str(HERE / "state.json"))
    parser.add_argument("--once", action="store_true", help="chạy một vòng rồi thoát")
    parser.add_argument("--replay", metavar="OWNER/REPO#N")
    parser.add_argument("--diff-file", metavar="FILE")
    parser.add_argument("--repo", help="repo trong config dùng cho --diff-file")
    parser.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--no-telegram", action="store_true",
                        help="chỉ in ra màn hình, không gửi Telegram")
    parser.add_argument("--require-telegram", action="store_true",
                        help="dừng với lỗi nếu chưa cấu hình Telegram")
    args = parser.parse_args(argv)

    # Windows: không để ký tự lạ làm bot dừng khi in ra màn hình
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    load_env_file(HERE / ".env")
    cfg = load_config(Path(args.config))
    notifier = Notifier(
        None if args.no_telegram else os.environ.get("TELEGRAM_BOT_TOKEN"),
        None if args.no_telegram else os.environ.get("TELEGRAM_CHAT_ID"))

    if (args.test_telegram or args.require_telegram) and not notifier.enabled:
        raise SystemExit("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
    if args.test_telegram:
        return 0 if notifier.send("✅ listing-watcher đã kết nối Telegram.") else 1
    if args.diff_file:
        cmd_diff_file(cfg, args, notifier)
        return 0

    token = os.environ.get("GITHUB_TOKEN")
    state_path = Path(args.state)
    state = load_state(state_path)
    gh = GitHub(token, state.setdefault("etags", {}))

    if args.replay:
        cmd_replay(gh, cfg, args, notifier)
        return 0

    interval = cfg["poll_seconds"]
    if not token:
        interval = max(interval, 180 * len(cfg["repos"]))
        print("[lưu ý] chưa có GITHUB_TOKEN: GitHub chỉ cho 60 yêu cầu/giờ, "
              f"nên bot giãn chu kỳ lên {interval} giây.")
    if not notifier.enabled:
        print("[lưu ý] chưa cấu hình Telegram: cảnh báo chỉ in ra màn hình.")

    errors = 0
    while True:
        try:
            errors = run_cycle(gh, cfg, state, notifier)
        except RateLimited as limit:
            wait = max(5, limit.reset_epoch - time.time() + 2)
            print(f"[giới hạn] GitHub tạm chặn, chờ {wait:.0f} giây.")
            save_state(state_path, state)
            if args.once:
                return 1
            time.sleep(wait)
            continue
        except KeyboardInterrupt:
            break
        save_state(state_path, state)
        if args.once:
            return 1 if errors else 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            break
    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
