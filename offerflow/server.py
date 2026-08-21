#!/usr/bin/env python3
"""OfferFlow 本地服务：提供静态页面和招聘链接解析接口。"""

from __future__ import annotations

import gzip
import base64
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from html import unescape
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


HOST = os.environ.get("OFFERFLOW_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
DEBUG_HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parent
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 15
BROWSER_TIMEOUT_SECONDS = 22
CHROME_CANDIDATES = [
    Path(os.environ.get("CHROME_BIN", "/nonexistent")),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/google-chrome"),
]
BROWSER_SEMAPHORE = threading.BoundedSemaphore(
    max(1, int(os.environ.get("BROWSER_MAX_CONCURRENCY", "2")))
)
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = max(5, int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30")))
RATE_LIMIT_LOCK = threading.Lock()
RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}


class JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.json_ld: list[str] = []
        self.scripts: list[str] = []
        self.text_parts: list[str] = []
        self._capture_title = False
        self._capture_script: str | None = None
        self._buffer: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self._capture_title = True
            self._buffer = []
        elif tag == "meta":
            key = (
                attrs_dict.get("property")
                or attrs_dict.get("name")
                or attrs_dict.get("itemprop")
                or ""
            ).lower()
            content = attrs_dict.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        elif tag == "script":
            script_type = attrs_dict.get("type", "").lower()
            script_id = attrs_dict.get("id", "").lower()
            if script_type == "application/ld+json":
                self._capture_script = "json_ld"
            elif script_id in {"__next_data__", "__nuxt_data__"} or "json" in script_type:
                self._capture_script = "script"
            else:
                self._capture_script = None
            self._buffer = []
            self._skip_depth += 1
        elif tag in {"style", "noscript", "svg"}:
            self._skip_depth += 1
        elif tag in {"p", "li", "br", "h1", "h2", "h3", "h4", "section", "div"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._capture_title:
            self.title = clean_text("".join(self._buffer))
            self._capture_title = False
            self._buffer = []
        elif tag == "script":
            value = "".join(self._buffer).strip()
            if value and self._capture_script == "json_ld":
                self.json_ld.append(value)
            elif value and self._capture_script == "script":
                self.scripts.append(value)
            self._capture_script = None
            self._buffer = []
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in {"style", "noscript", "svg"}:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._capture_title or self._capture_script:
            self._buffer.append(data)
        elif self._skip_depth == 0:
            text = data.strip()
            if text:
                self.text_parts.append(text)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def html_to_text(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|li|div|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return clean_text(text)


def validate_public_url(raw_url: str) -> str:
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("请输入完整的 http 或 https 招聘链接")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise ValueError("无法解析这个网址") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            raise ValueError("仅支持公开招聘网站链接")
    return raw_url


def fetch_page(url: str) -> tuple[str, str, int]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    def open_request(context: ssl.SSLContext):
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context)
        )
        return opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        try:
            response_context = open_request(ssl.create_default_context())
        except urllib.error.URLError as exc:
            if not isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
                raise
            # 某些企业网络会注入系统已信任、但 Python 未识别的代理证书。
            # 仅在明确的证书链校验错误时，对当前公开 URL 做一次兼容重试。
            response_context = open_request(ssl._create_unverified_context())

        with response_context as response:
            status = response.status
            final_url = response.geturl()
            validate_public_url(final_url)
            content_type = response.headers.get("Content-Type", "")
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("招聘页面内容过大，暂不支持自动解析")
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                raw = zlib.decompress(raw)
            charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
            charset = charset_match.group(1) if charset_match else "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            return text, final_url, status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("岗位页面不存在或已经下线") from exc
        if exc.code in {401, 403}:
            raise ValueError("招聘网站要求登录或拒绝自动读取") from exc
        raise ValueError(f"招聘网站返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ValueError(f"无法连接招聘网站：{reason}") from exc


def find_browser() -> Path:
    browser = next((path for path in CHROME_CANDIDATES if path.exists()), None)
    if not browser:
        executable = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        browser = Path(executable) if executable else None
    if not browser:
        raise ValueError("未找到 Chrome、Edge 或 Chromium，无法进行浏览器渲染")
    return browser


class CDPWebSocket:
    """仅实现本地 Chrome DevTools 所需的最小 WebSocket 客户端。"""

    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
            if len(response) > 16384:
                break
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            self.close()
            raise ValueError("无法连接 Chrome 调试端口")
        self.next_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _recv_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ValueError("Chrome 调试连接已关闭")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_frame(self, payload: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._recv_exact(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if second & 0x80 else None
        payload = self._recv_exact(length)
        if mask:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return opcode, payload

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        self._send_frame(
            json.dumps(
                {"id": message_id, "method": method, "params": params or {}}
            ).encode("utf-8")
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            opcode, payload = self._recv_frame()
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode == 8:
                raise ValueError("Chrome 提前关闭了调试连接")
            if opcode != 1:
                continue
            message = json.loads(payload.decode("utf-8", errors="replace"))
            if message.get("id") == message_id:
                if "error" in message:
                    raise ValueError(message["error"].get("message", "浏览器执行失败"))
                return message.get("result", {})
        raise ValueError("等待 Chrome 页面响应超时")


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEBUG_HOST, 0))
        return int(sock.getsockname()[1])


def wait_for_debug_target(port: int) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://{DEBUG_HOST}:{port}/json/list", timeout=1
            ) as response:
                targets = json.loads(response.read())
                page = next(
                    (
                        target
                        for target in targets
                        if target.get("type") == "page"
                        and target.get("webSocketDebuggerUrl")
                    ),
                    None,
                )
                if page:
                    return page
        except Exception as exc:
            last_error = exc
        time.sleep(0.15)
    raise ValueError(f"Chrome 调试端口未就绪：{last_error or '超时'}")


def render_page(url: str) -> tuple[str, str]:
    """用独立无头浏览器渲染动态页面，并返回最终 DOM。"""
    browser = find_browser()
    if not BROWSER_SEMAPHORE.acquire(timeout=BROWSER_TIMEOUT_SECONDS):
        raise ValueError("浏览器解析任务繁忙，请稍后重试")
    profile_dir = Path(tempfile.mkdtemp(prefix="offerflow-browser-"))
    debug_port = available_port()
    command = [
        str(browser),
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-crash-reporter",
        "--disable-breakpad",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        "--no-default-browser-check",
        "--ignore-certificate-errors",
        "--hide-scrollbars",
        "--window-size=1440,1200",
        f"--remote-debugging-port={debug_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        f"--disk-cache-dir={profile_dir / 'cache'}",
        "about:blank",
    ]
    process: subprocess.Popen[str] | None = None
    websocket: CDPWebSocket | None = None
    try:
        browser_env = os.environ.copy()
        browser_env.update(
            {
                "HOME": str(profile_dir),
                "XDG_CONFIG_HOME": str(profile_dir / "config"),
                "XDG_CACHE_HOME": str(profile_dir / "cache"),
            }
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=browser_env,
        )
        target = wait_for_debug_target(debug_port)
        websocket = CDPWebSocket(target["webSocketDebuggerUrl"])
        websocket.call("Page.enable")
        websocket.call("Runtime.enable")
        websocket.call("Page.navigate", {"url": url})
        time.sleep(8)
        evaluation = websocket.call(
            "Runtime.evaluate",
            {
                "expression": (
                    "JSON.stringify({html:document.documentElement.outerHTML,"
                    "url:location.href,title:document.title})"
                ),
                "returnByValue": True,
            },
        )
        value = evaluation.get("result", {}).get("value", "")
        rendered = json.loads(value) if value else {}
        html = rendered.get("html", "")
        final_url = rendered.get("url", url)
        if not html or len(html) < 200:
            raise ValueError("浏览器已打开页面，但没有获得可解析的内容")
        if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES * 3:
            raise ValueError("浏览器渲染后的页面过大，暂不支持自动解析")
        validate_public_url(final_url)
        return html, final_url
    except OSError as exc:
        raise ValueError(f"无法启动本地浏览器：{exc}") from exc
    finally:
        if websocket:
            websocket.close()
        if process and process.poll() is None:
            process.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)
        BROWSER_SEMAPHORE.release()


def walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def is_job_posting(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    item_type = value.get("@type")
    if item_type == "JobPosting":
        return True
    if isinstance(item_type, list) and "JobPosting" in item_type:
        return True
    keys = {key.lower() for key in value}
    return (
        ("jobdescription" in keys or "description" in keys)
        and ("jobtitle" in keys or "title" in keys)
        and ("location" in keys or "joblocation" in keys)
    )


def load_embedded_json(parser: JobPageParser) -> list[Any]:
    values: list[Any] = []
    for raw in parser.json_ld + parser.scripts:
        try:
            values.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return values


def first_value(data: dict[str, Any], keys: list[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", [], {}):
            return value
    return ""


def organization_name(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(first_value(value, ["name", "companyName", "organizationName"]))
    return ""


def location_text(value: Any) -> str:
    values = value if isinstance(value, list) else [value] if value else []
    locations: list[str] = []
    for item in values:
        if isinstance(item, str):
            locations.append(clean_text(item))
            continue
        if not isinstance(item, dict):
            continue
        address = first_value(item, ["address", "location", "jobLocation"])
        if isinstance(address, str):
            locations.append(clean_text(address))
        elif isinstance(address, dict):
            parts = [
                first_value(address, ["addressCountry", "country"]),
                first_value(address, ["addressRegion", "region", "province"]),
                first_value(address, ["addressLocality", "city"]),
                first_value(address, ["streetAddress", "address"]),
            ]
            locations.append(" · ".join(clean_text(part) for part in parts if part))
        else:
            direct = first_value(item, ["name", "city", "locationName"])
            if direct:
                locations.append(clean_text(direct))
    return " / ".join(dict.fromkeys(item for item in locations if item))


def find_labeled_value(text: str, labels: list[str]) -> str:
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[：:]?\s*([^\n]{{2,120}})", text, re.I
        )
        if match:
            return clean_text(match.group(1))
    return ""


def likely_description(text: str) -> str:
    markers = ["职位描述", "岗位职责", "工作职责", "职位职责", "任职要求", "职位要求"]
    starts = [text.find(marker) for marker in markers if text.find(marker) >= 0]
    if starts:
        return clean_text(text[min(starts) : min(starts) + 12000])
    english_markers = ["Responsibilities", "Qualifications", "Job Description"]
    starts = [text.find(marker) for marker in english_markers if text.find(marker) >= 0]
    if starts:
        return clean_text(text[min(starts) : min(starts) + 12000])
    return ""


def infer_company_from_host(hostname: str) -> str:
    known = {
        "jobs.bytedance.com": "字节跳动",
        "job.toutiao.com": "字节跳动",
        "careers.tencent.com": "腾讯",
        "talent.alibaba.com": "阿里巴巴",
        "career.huawei.com": "华为",
    }
    for host, name in known.items():
        if hostname == host or hostname.endswith("." + host):
            return name
    return ""


def extract_bytedance_fields(body_text: str) -> dict[str, str]:
    """解析字节招聘动态详情页头部：职位名、地点、JD。"""
    lines = [clean_text(line) for line in body_text.splitlines() if clean_text(line)]
    detail_markers = {
        "Responsibilities",
        "Qualifications",
        "职位描述",
        "岗位职责",
        "工作职责",
        "职位职责",
        "任职要求",
        "职位要求",
    }
    marker_index = next(
        (index for index, line in enumerate(lines) if line in detail_markers),
        -1,
    )
    if marker_index < 0:
        return {}

    header = lines[:marker_index]
    employment_types = {
        "Regular",
        "Intern",
        "Full-time",
        "Part-time",
        "正式",
        "实习",
        "兼职",
    }
    employment_index = next(
        (
            index
            for index, line in enumerate(header)
            if line in employment_types and index >= 2
        ),
        -1,
    )
    if employment_index >= 2:
        position = header[employment_index - 2]
        location = header[employment_index - 1]
    else:
        position = ""
        location = ""

    ignored = {
        "Sign in",
        "登录",
        "首页",
        "技术人才项目",
        "职位",
        "招聘动态",
        "产品与技术",
        "成长与回报",
        "社会招聘",
        "Regular",
        "Intern",
        "Full-time",
        "Part-time",
        "正式",
        "实习",
        "校招",
        "社招",
    }
    useful = [
        line
        for line in header
        if line not in ignored
        and not re.match(r"^(Job ID|职位 ID|职位编号)\s*[：:]?", line, re.I)
        and not re.match(r"^(R&D|Product|Operations|Marketing|Design|Sales)\b", line)
    ]
    if not useful:
        return {}

    position = position or useful[0]
    if not location and len(useful) > 1:
        candidate = useful[1]
        if (
            len(candidate) <= 100
            and not re.search(r"(Campus Recruitment|Graduate|Job ID|职位)", candidate, re.I)
        ):
            location = candidate

    stop_markers = {"投递", "相关职位", "Apply", "Apply now", "Related Jobs"}
    end_index = next(
        (
            index
            for index in range(marker_index + 1, len(lines))
            if lines[index] in stop_markers
        ),
        min(marker_index + 300, len(lines)),
    )
    jd = clean_text("\n".join(lines[marker_index:end_index]))
    return {"position": position, "location": location, "jd": jd}


def extract_job(html: str, final_url: str) -> dict[str, str]:
    final_parts = urllib.parse.urlparse(final_url)
    final_query = urllib.parse.parse_qs(final_parts.query)
    if final_query.get("error", [""])[0].lower() == "true":
        raise ValueError("岗位页面不存在或已经下线")

    parser = JobPageParser()
    parser.feed(html)
    body_text = clean_text("\n".join(parser.text_parts))

    candidates: list[dict[str, Any]] = []
    for embedded in load_embedded_json(parser):
        candidates.extend(item for item in walk_json(embedded) if is_job_posting(item))
    job = max(candidates, key=lambda item: len(json.dumps(item, ensure_ascii=False)), default={})
    has_structured_job = bool(job)

    position = clean_text(
        first_value(job, ["title", "jobTitle", "positionName", "name"])
    )
    company = organization_name(
        first_value(job, ["hiringOrganization", "company", "organization", "companyName"])
    )
    location = location_text(
        first_value(job, ["jobLocation", "location", "locations", "workLocation"])
    )
    jd = html_to_text(
        first_value(job, ["description", "jobDescription", "responsibilities", "content"])
    )

    position = position or clean_text(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.title.split(" - ")[0].split(" | ")[0]
    )
    company = company or clean_text(
        parser.meta.get("og:site_name")
        or parser.meta.get("application-name")
        or infer_company_from_host(urllib.parse.urlparse(final_url).hostname or "")
    )
    location = location or find_labeled_value(
        body_text, ["工作地点", "工作地址", "办公地点", "职位地点", "地点", "Location"]
    )
    jd = jd or likely_description(body_text)

    hostname = final_parts.hostname or ""
    if hostname == "jobs.bytedance.com" or hostname.endswith(".jobs.bytedance.com"):
        bytedance = extract_bytedance_fields(body_text)
        position = bytedance.get("position") or position
        location = bytedance.get("location") or location
        jd = bytedance.get("jd") or jd

    if "您正在寻找的页面不存在" in body_text or "页面不存在" in parser.title:
        raise ValueError("岗位页面不存在或已经下线")
    generic_title = any(
        marker in position
        for marker in ["招聘官网", "校园招聘", "社会招聘", "职位列表", "招聘首页"]
    )
    if not has_structured_job and not jd and not location and generic_title:
        raise ValueError("没有读取到具体岗位，链接可能不完整或岗位已经下线")

    result = {
        "company": company[:80],
        "position": position[:120],
        "jd": jd[:12000],
        "location": location[:120],
        "sourceUrl": final_url,
    }
    if not result["position"] and not result["jd"]:
        raise ValueError("页面已读取，但没有识别到公开岗位信息")
    return result


def parse_job_with_fallback(url: str) -> dict[str, str]:
    """优先静态读取；无具体岗位时再使用浏览器渲染。"""
    static_error: ValueError | None = None
    static_result: dict[str, str] = {}
    try:
        html, final_url, _ = fetch_page(url)
        static_result = extract_job(html, final_url)
        if (
            static_result.get("position")
            and static_result.get("jd")
            and static_result.get("location")
        ):
            static_result["parseMethod"] = "static"
            return static_result
    except ValueError as exc:
        static_error = exc

    try:
        rendered_html, final_url = render_page(url)
        browser_result = extract_job(rendered_html, final_url)
        result = {
            key: browser_result.get(key) or static_result.get(key, "")
            for key in ("company", "position", "jd", "location", "sourceUrl")
        }
        result["parseMethod"] = "browser"
        return result
    except ValueError as browser_error:
        if "岗位" in str(browser_error) or "页面" in str(browser_error):
            raise browser_error
        raise ValueError(
            f"静态读取失败：{static_error}；浏览器渲染失败：{browser_error}"
        ) from browser_error


def parse_resume_file(filename: str, encoded_content: str) -> dict[str, str]:
    """从常见简历文件中提取纯文本，文件只在本机内存或临时目录中处理。"""
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise ValueError("仅支持 PDF、DOCX、TXT 和 MD 格式")
    try:
        content = base64.b64decode(encoded_content, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("简历文件内容无法识别") from exc
    if not content or len(content) > 8 * 1024 * 1024:
        raise ValueError("简历文件为空或超过 8MB")

    if suffix in {".txt", ".md"}:
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            raise ValueError("文本文件编码无法识别")
    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paragraphs = []
            for paragraph in root.iter(f"{namespace}p"):
                parts = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
                line = "".join(parts).strip()
                if line:
                    paragraphs.append(line)
            text = "\n".join(paragraphs)
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise ValueError("DOCX 文件损坏或格式不正确") from exc
    else:
        converter = shutil.which("pdftotext")
        if not converter:
            raise ValueError("当前电脑缺少 PDF 文本提取工具，请改用 DOCX 或 TXT")
        temp_dir = Path(tempfile.mkdtemp(prefix="offerflow-resume-"))
        try:
            source = temp_dir / "resume.pdf"
            output = temp_dir / "resume.txt"
            source.write_bytes(content)
            completed = subprocess.run(
                [converter, "-layout", str(source), str(output)],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if completed.returncode != 0 or not output.exists():
                raise ValueError("PDF 文本提取失败，扫描版 PDF 请先转换为可复制文本")
            text = output.read_text("utf-8", errors="replace")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 20:
        raise ValueError("未提取到足够的简历文字，扫描版文件请先进行 OCR")
    return {"filename": safe_name, "text": text[:100000]}


def request_allowed(client_ip: str) -> bool:
    """简单的单实例限流，避免公开解析接口被短时间滥用。"""
    current = time.monotonic()
    cutoff = current - RATE_LIMIT_WINDOW_SECONDS
    with RATE_LIMIT_LOCK:
        bucket = [timestamp for timestamp in RATE_LIMIT_BUCKETS.get(client_ip, []) if timestamp >= cutoff]
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            RATE_LIMIT_BUCKETS[client_ip] = bucket
            return False
        bucket.append(current)
        RATE_LIMIT_BUCKETS[client_ip] = bucket
        if len(RATE_LIMIT_BUCKETS) > 2000:
            stale = [key for key, values in RATE_LIMIT_BUCKETS.items() if not values or values[-1] < cutoff]
            for key in stale[:1000]:
                RATE_LIMIT_BUCKETS.pop(key, None)
        return True


class OfferFlowHandler(SimpleHTTPRequestHandler):
    server_version = "OfferFlow"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "font-src 'self' data:; connect-src 'self'; "
            "frame-src https://www.nowcoder.com; object-src 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
        if self.path.startswith("/api/") or self.path.startswith("/healthz"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        if urllib.parse.urlparse(self.path).path == "/healthz":
            try:
                browser_ready = bool(find_browser())
            except ValueError:
                browser_ready = False
            pdf_ready = bool(shutil.which("pdftotext"))
            healthy = browser_ready and pdf_ready
            self.send_json(
                200 if healthy else 503,
                {
                    "ok": healthy,
                    "service": "offerflow",
                    "browser": browser_ready,
                    "pdf": pdf_ready,
                },
            )
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path not in {"/api/parse-job", "/api/parse-resume"}:
            self.send_error(404)
            return
        try:
            forwarded = self.headers.get("X-Forwarded-For", "")
            client_ip = forwarded.split(",", 1)[0].strip() or self.client_address[0]
            if not request_allowed(client_ip):
                self.send_json(429, {"ok": False, "error": "请求过于频繁，请稍后重试"})
                return
            content_length = int(self.headers.get("Content-Length", "0"))
            max_length = 12 * 1024 * 1024 if self.path == "/api/parse-resume" else 16 * 1024
            if content_length <= 0 or content_length > max_length:
                raise ValueError("请求内容不正确")
            payload = json.loads(self.rfile.read(content_length))
            if self.path == "/api/parse-resume":
                result = parse_resume_file(
                    str(payload.get("filename", "")).strip(),
                    str(payload.get("content", "")),
                )
            else:
                url = validate_public_url(str(payload.get("url", "")).strip())
                result = parse_job_with_fallback(url)
            self.send_json(200, {"ok": True, "data": result})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(422, {"ok": False, "error": str(exc)})
        except Exception as exc:
            print(f"解析失败: {exc}", file=sys.stderr)
            self.send_json(500, {"ok": False, "error": "解析服务发生错误"})

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), OfferFlowHandler)
    print(f"OfferFlow 已启动：http://{HOST}:{PORT}")
    print("按 Control+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nOfferFlow 已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
