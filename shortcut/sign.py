#!/usr/bin/env python3
"""把 build_shortcut.py 生成的 XML plist 签名成可导入 iOS 的 .shortcut。

签名是硬门槛：iOS 15 起，未签名的 .shortcut 无法导入。
本机没有任何 Apple 签名密钥，也不允许安装第三方二进制，
所以借用 Cherri Playground 的公开 /sign 接口完成签名：

    GET  /sanctum/csrf-cookie   拿 XSRF-TOKEN + session
    POST /sign  {"name":..,"data":..}   返回 /storage/<uuid>/<name>.shortcut
    GET  该路径                 拿回 AEA1 容器

用法：
    python3 shortcut/build_shortcut.py /tmp/a.plist
    python3 shortcut/sign.py /tmp/a.plist shortcut/排班闹钟.shortcut
"""

import http.client
import json
import socket
import ssl
import sys
import time
import urllib.parse
from pathlib import Path

HOST = "playground.cherrilang.org"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36"
NAME = "paiban-alarm"   # 必须是 ASCII：中文会让下载路径编码失败


def _open(host=HOST, timeout=120):
    """新建一条到 playground 的 HTTPS 连接（proot 环境下按 IP 直连更稳）。"""
    ip = socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0]
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(socket.create_connection((ip, 443), timeout=20),
                           server_hostname=host)
    conn = http.client.HTTPConnection(host, 443, timeout=timeout)
    conn.sock = sock
    return conn


def _csrf() -> dict:
    """取 CSRF cookie，返回 {cookie名: 值}。"""
    conn = _open()
    conn.request("GET", "/sanctum/csrf-cookie",
                 headers={"User-Agent": UA, "Accept": "application/json",
                          "Connection": "close"})
    resp = conn.getresponse()
    resp.read()
    cookies = {}
    for key, val in resp.getheaders():
        if key.lower() == "set-cookie":
            n, v = val.split(";")[0].split("=", 1)
            cookies[n] = v
    if "XSRF-TOKEN" not in cookies:
        raise SystemExit(f"取不到 CSRF cookie，HTTP {resp.status}")
    return cookies


def sign(plist_text: str, name: str = NAME) -> bytes:
    cookies = _csrf()
    body = json.dumps({"name": name, "data": plist_text}, ensure_ascii=False)

    conn = _open()
    conn.request("POST", "/sign", body=body.encode("utf-8"), headers={
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "X-XSRF-TOKEN": urllib.parse.unquote(cookies["XSRF-TOKEN"]),
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "Referer": f"https://{HOST}/",
        "Origin": f"https://{HOST}",
        "Connection": "close",
    })
    resp = conn.getresponse()
    raw = resp.read().decode().strip().strip('"').replace("\\/", "/")
    if resp.status != 200 or not raw.startswith("/"):
        raise SystemExit(f"签名失败，HTTP {resp.status}：{raw[:300]}")

    path = urllib.parse.quote(raw, safe="/:")
    conn = _open()
    conn.request("GET", path, headers={"User-Agent": UA, "Connection": "close"})
    resp = conn.getresponse()
    data = resp.read()
    if resp.status != 200:
        raise SystemExit(f"下载失败，HTTP {resp.status}")
    if not data.startswith(b"AEA1"):
        raise SystemExit(f"返回的不是签名容器，首字节 {data[:8]!r}")
    return data


def main(argv) -> None:
    src = Path(argv[1] if len(argv) > 1 else "/tmp/paiban_alarm.plist")
    dst = Path(argv[2] if len(argv) > 2 else "shortcut/排班闹钟.shortcut")
    text = src.read_text(encoding="utf-8")
    for attempt in range(1, 4):
        try:
            data = sign(text)
            break
        except (OSError, http.client.HTTPException) as exc:
            if attempt == 3:
                raise
            print(f"第 {attempt} 次失败（{exc}），重试…", file=sys.stderr)
            time.sleep(5)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    print(f"已签名 {dst}（{len(data)} 字节，AEA1）")


if __name__ == "__main__":
    main(sys.argv)
