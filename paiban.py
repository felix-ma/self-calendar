#!/usr/bin/env python3
"""排班表截图 -> 日历文件(.ics)。

识别：把图片发给 DeepSeek 多模态模型（OpenAI 兼容接口），拿回结构化排班。
生成：纯 Python 标准库拼 .ics，不需要任何第三方依赖。

用法：
    python3 paiban.py recognize images/*.png -o schedule.json
    python3 paiban.py build schedule.json -o docs/schedule.ics
    python3 paiban.py update            # 识别 images/ 再重新生成
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

API_BASE = os.environ.get("PAIBAN_API_BASE", "https://api.deepseek.com")
MODEL = os.environ.get("PAIBAN_MODEL", "deepseek-flash")
TZID = "Asia/Shanghai"
TZOFF = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
PLACES_FILE = ROOT / "places.json"

# 班次时段（统一时段，可在 build 时覆盖）
SHIFTS = {
    "早班": {"start": "08:00", "end": "15:00"},
    "晚班": {"start": "15:00", "end": "22:00"},
    "白班": {"start": "08:00", "end": "17:00",
             "note": "8:00-12:00、14:00-17:00，12:00-14:00 休息"},
}
ALIASES = {"班一": "早班", "班二": "晚班", "班三": "白班",
           "休": "休息", "休息": "休息", "": "休息"}

PROMPT = """你是排班表识别助手。图片是一张 Excel 排班表截图，通常一行 7 天。
表头第 1 行是日期（形如 10-19、10/26、11-2），第 2 行是星期（一、二、三、四、五、六、日）。
日期下面每个员工占两行：第一行是姓名和班次，第二行是门店名。
班次用颜色和文字标注：
- 班一 = 早班（绿色）
- 班二 = 晚班（黄色）
- 班三 = 白班（粉色）
- 红色「休息」= 休息
门店只会是：和风、悦兴、华锦、世纪、泽润。

只输出一个 JSON 对象，不要解释、不要 markdown 代码块：
{"days":[{"date":"MM-DD","weekday":"周一","shift":"早班|晚班|白班|休息","store":"门店名，休息时为空字符串"}]}

要求：
1. days 必须包含图里全部 7 天，按日期从左到右排列；
2. date 只写月和日，不要年份；
3. shift 只能是 早班、晚班、白班、休息 四个值之一。
"""


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- 识别
def _post_json(url: str, payload: dict, key: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        die(f"接口返回 HTTP {e.code}：{body}")
    except urllib.error.URLError as e:
        die(f"连不上接口：{e.reason}")


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        die(f"模型没有返回 JSON：{text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        die(f"JSON 解析失败：{e}\n{text[:400]}")


def recognize_image(path: Path, key: str) -> dict:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
    }
    effort = os.environ.get("PAIBAN_REASONING_EFFORT", "low")
    if effort:
        payload["reasoning_effort"] = effort
    data = _post_json(API_BASE.rstrip("/") + "/chat/completions", payload, key)
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError):
        die(f"接口返回格式异常：{json.dumps(data, ensure_ascii=False)[:300]}")
    if not content.strip():
        die("模型返回了空内容，重试一次或调大 PAIBAN_MAX_TOKENS")
    return _extract_json(content)


def _parse_md(text: str) -> tuple[int, int]:
    m = re.match(r"\s*(?:(\d{4})[-/年])?\s*(\d{1,2})[-/月](\d{1,2})", str(text))
    if not m:
        raise ValueError(f"看不懂日期：{text!r}")
    return int(m.group(2)), int(m.group(3))


def _pick(d: dict, *names):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None


def _infer_year(month: int, day: int, today: date) -> int:
    """首列是周一：在附近年份里找出这一天确实是周一的年份。"""
    for delta in (0, -1, 1, -2, 2, -3, 3):
        y = today.year + delta
        try:
            if date(y, month, day).weekday() == 0:
                return y
        except ValueError:
            continue
    return today.year


def normalize_week(raw: dict, today: date | None = None) -> dict:
    today = today or date.today()
    if not isinstance(raw, dict):
        raise ValueError("模型返回的不是对象")
    days = _pick(raw, "days", "天", "天数", "日程", "排班", "records") or []
    if len(days) != 7:
        raise ValueError(f"识别结果不是 7 天（{len(days)} 天）：{json.dumps(raw, ensure_ascii=False)[:300]}")
    pairs = [_parse_md(_pick(d, "date", "日期") or "") for d in days]
    year = _infer_year(*pairs[0], today)
    out_days = []
    cur_year = year
    prev_month = pairs[0][0]
    for i, d in enumerate(days):
        m, dd = pairs[i]
        if i > 0 and m < prev_month:  # 跨年
            cur_year += 1
        prev_month = m
        raw_shift = str(_pick(d, "shift", "班次", "班") or "").strip()
        shift = ALIASES.get(raw_shift, raw_shift)
        if shift not in SHIFTS and shift != "休息":
            raise ValueError(f"未知班次：{raw_shift!r}")
        try:
            iso = date(cur_year, m, dd).isoformat()
        except ValueError:
            raise ValueError(f"无效日期：{cur_year}-{m}-{dd}")
        store = "" if shift == "休息" else str(_pick(d, "store", "门店", "店铺", "店名") or "").strip()
        out_days.append({
            "date": iso,
            "weekday": str(_pick(d, "weekday", "星期", "周几") or ""),
            "shift": shift,
            "store": store,
        })
    ds = [date.fromisoformat(x["date"]) for x in out_days]
    for a, b in zip(ds, ds[1:]):
        if (b - a).days != 1:
            raise ValueError(f"日期不连续：{a} -> {b}")
    return {"days": out_days}


def cmd_recognize(args) -> None:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        die("没有设置 DEEPSEEK_API_KEY 环境变量")
    images = [Path(p) for p in args.images]
    for p in images:
        if not p.exists():
            die(f"找不到图片：{p}")
    merged: dict[str, dict] = {}
    for p in sorted(images):
        print(f"识别 {p.name} ...", file=sys.stderr)
        week = None
        for attempt in range(2):
            try:
                week = normalize_week(recognize_image(p, key))
                break
            except ValueError as e:
                if attempt == 0:
                    print(f"  结果不完整（{e}），重试一次", file=sys.stderr)
                else:
                    die(str(e))
        assert week is not None
        for d in week["days"]:
            if d["date"] in merged and merged[d["date"]]["shift"] != d["shift"]:
                print(f"  警告：{d['date']} 两图班次不一致，保留后一张", file=sys.stderr)
            merged[d["date"]] = d
    schedule = {
        "timezone": TZID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shifts": SHIFTS,
        "days": [merged[k] for k in sorted(merged)],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {out}（{len(schedule['days'])} 天）", file=sys.stderr)


# ---------------------------------------------------------------- 生成
def _esc(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """按 RFC 5545 折行：每行不超过 75 字节，UTF-8 字符不拆开。"""
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 73:
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _hm(t: str) -> str:
    h, m = t.split(":")[:2]
    return f"{int(h):02d}{int(m):02d}00"


def load_places() -> dict:
    if PLACES_FILE.exists():
        try:
            return json.loads(PLACES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


COMMUTE_FILE = ROOT / "commute.json"
_COMMUTE = None


def load_commute() -> dict:
    global _COMMUTE
    if _COMMUTE is None:
        try:
            _COMMUTE = json.loads(COMMUTE_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            _COMMUTE = {}
    return _COMMUTE


def parse_alarm(v):
    """auto / off / 分钟数；auto 按通勤时间动态算。"""
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("", "auto"):
        return None
    if s in ("off", "0", "none", "no"):
        return 0
    try:
        return max(0, int(s))
    except ValueError:
        return None


def alarm_info(e: dict, fixed=None):
    """返回 (提前分钟, 交通方式, 距离km)。5km 内电动车，更远打车。"""
    if fixed is not None:
        return fixed, "", 0.0
    c = load_commute()
    info = (c.get("stores") or {}).get(e["store"]) or {}
    if not info:
        return 0, "", 0.0
    km = float(info.get("km") or 0)
    drive = float(info.get("drive_min") or 0)
    modes = c.get("modes") or {}
    eb = modes.get("ebike") or {}
    tx = modes.get("taxi") or {}
    if km <= float(eb.get("max_km", 5)):
        label = eb.get("label", "电动车")
        base = km / float(eb.get("speed_kmh", 16)) * 60 + float(eb.get("extra_min", 5))
    else:
        label = tx.get("label", "打车")
        base = drive * float(tx.get("traffic_factor", 1.8)) + float(tx.get("extra_min", 8))
    minutes = int(round(base / 5.0)) * 5
    minutes = max(int(c.get("min_alarm", 15)), min(int(c.get("max_alarm", 180)), minutes))
    return minutes, label, km


def amap_search_url(keyword: str) -> str:
    return ("https://uri.amap.com/search?keyword="
            + urllib.parse.quote(keyword, safe="") + "&city=440300")


def _stamp(schedule: dict) -> str:
    """用识别时间做 DTSTAMP，保证同一份数据生成结果一致。"""
    gen = schedule.get("generated_at")
    if gen:
        try:
            return datetime.fromisoformat(gen).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def collect_events(schedule: dict, keep_all: bool = False) -> tuple[list, int]:
    days = [d for d in schedule.get("days", []) if d.get("shift") != "休息"]
    places = load_places()
    now = datetime.now(TZOFF)
    out, skipped = [], 0
    for d in sorted(days, key=lambda x: (x["date"], x["shift"])):
        shift = d["shift"]
        cfg = SHIFTS.get(shift)
        if not cfg:
            continue
        day = date.fromisoformat(d["date"])
        start_dt = datetime.combine(day, dtime.fromisoformat(cfg["start"]), tzinfo=TZOFF)
        end_dt = datetime.combine(day, dtime.fromisoformat(cfg["end"]), tzinfo=TZOFF)
        if not keep_all and end_dt <= now:
            skipped += 1
            continue
        store = d.get("store", "") or ""
        place = places.get(store, {})
        out.append({
            "day": day,
            "shift": shift,
            "cfg": cfg,
            "store": store,
            "full": place.get("full") or (store + "社康" if store else ""),
            "addr": place.get("address", ""),
            "uid": hashlib.sha1(f"{d['date']}|{shift}|{store}".encode()).hexdigest()[:20] + "@paiban2ics",
        })
    return out, skipped


def _calendar_header() -> list:
    return [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//paiban2ics//DeepSeek Vision//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
        "X-WR-CALNAME:排班",
        f"X-WR-TIMEZONE:{TZID}",
        "BEGIN:VTIMEZONE",
        f"TZID:{TZID}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def _event_lines(e: dict, stamp: str, fixed_alarm=None) -> list:
    cfg, day, shift = e["cfg"], e["day"], e["shift"]
    store, full, addr = e["store"], e["full"], e["addr"]
    summary = shift + (f"·{store}" if store else "")
    minutes, mode, km = alarm_info(e, fixed_alarm)
    desc = f"班次：{shift} {cfg['start']}-{cfg['end']}"
    if cfg.get("note"):
        desc += f"（{cfg['note']}）"
    if store:
        desc += f"\n门店：{full}"
        if addr:
            desc += f"\n地址：{addr}"
    if minutes:
        desc += f"\n通勤：{mode} {km:.1f} km，预留 {minutes} 分钟"
    lines = [
        "BEGIN:VEVENT",
        f"UID:{e['uid']}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={TZID}:{day.strftime('%Y%m%d')}T{_hm(cfg['start'])}",
        f"DTEND;TZID={TZID}:{day.strftime('%Y%m%d')}T{_hm(cfg['end'])}",
        f"SUMMARY:{_esc(summary)}",
    ]
    if store:
        loc = f"{full}，{addr}" if addr else full
        lines.append(f"LOCATION:{_esc(loc)}")
        if addr:
            lines.append(f"URL:{amap_search_url(full + ' ' + addr)}")
    lines += [f"DESCRIPTION:{_esc(desc)}", "STATUS:CONFIRMED", "TRANSP:OPAQUE"]
    if minutes:
        label = "该出发了"
        if e["store"]:
            label += f" · {mode}到{e['store']}"
        lines += ["BEGIN:VALARM", f"TRIGGER:-PT{int(minutes)}M", "ACTION:DISPLAY",
                  f"DESCRIPTION:{_esc(label)}", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def _ics_text(lines: list) -> str:
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def build_ics(schedule: dict, path: Path, alarm=None, keep_all: bool = False) -> tuple[int, int]:
    events, skipped = collect_events(schedule, keep_all)
    if not events:
        die("没有可导入的班次（全是休息或都已结束）")
    lines = _calendar_header()
    stamp = _stamp(schedule)
    for e in events:
        lines += _event_lines(e, stamp, alarm)
    lines.append("END:VCALENDAR")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ics_text(lines), encoding="utf-8", newline="")
    return len(events), skipped


def event_ics(e: dict, stamp: str, alarm=None) -> str:
    return _ics_text(_calendar_header() + _event_lines(e, stamp, alarm) + ["END:VCALENDAR"])


# ---------------------------------------------------------------- CalDAV
CALDAV_ENV = Path.home() / ".config" / "paiban" / "caldav.env"
NS = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:caldav"}
_PRINCIPAL_XML = ('<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:">'
                  '<D:prop><D:current-user-principal/></D:prop></D:propfind>')
_HOME_XML = ('<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:" '
             'xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><C:calendar-home-set/>'
             '</D:prop></D:propfind>')
_LIST_XML = ('<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:" '
             'xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:displayname/>'
             '<D:resourcetype/></D:prop></D:propfind>')
_REPORT_XML = ('<?xml version="1.0" encoding="utf-8"?><C:calendar-query xmlns:D="DAV:" '
               'xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:getetag/>'
               '<C:calendar-data/></D:prop><C:filter><C:comp-filter name="VCALENDAR"/>'
               '</C:filter></C:calendar-query>')


def _read_env_file(path: Path) -> dict:
    d = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def caldav_config() -> tuple[str, str, str]:
    conf = _read_env_file(CALDAV_ENV)
    user = os.environ.get("ICLOUD_USER") or conf.get("ICLOUD_USER", "")
    pwd = os.environ.get("ICLOUD_PASS") or conf.get("ICLOUD_PASS", "")
    name = os.environ.get("ICLOUD_CAL") or conf.get("ICLOUD_CAL", "") or "排班表"
    if not user or not pwd:
        die(f"没有 CalDAV 凭据：设置 ICLOUD_USER / ICLOUD_PASS，或写入 {CALDAV_ENV}")
    return user, pwd, name


def _dav(method: str, url: str, body: str | None, user: str, pwd: str,
         depth: str = "0", ctype: str = "application/xml; charset=utf-8", timeout: int = 60):
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    headers = {"Authorization": "Basic " + auth, "User-Agent": "paiban2ics/1.0", "Depth": depth}
    if body is not None:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body.encode("utf-8") if body is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except urllib.error.URLError as e:
        die(f"连不上 CalDAV：{e.reason}")


def caldav_calendar_url(user: str, pwd: str, name: str, create: bool = True) -> str:
    base = "https://caldav.icloud.com"
    s, b = _dav("PROPFIND", base + "/", _PRINCIPAL_XML, user, pwd)
    if s not in (200, 207):
        die(f"CalDAV 认证失败：HTTP {s}")
    href = ET.fromstring(b).find(".//D:current-user-principal/D:href", NS)
    if href is None:
        die("找不到日历账户（current-user-principal）")
    s, b = _dav("PROPFIND", urllib.parse.urljoin(base + "/", href.text), _HOME_XML, user, pwd)
    home = ET.fromstring(b).find(".//C:calendar-home-set/D:href", NS)
    if home is None:
        die("找不到日历主目录（calendar-home-set）")
    home_url = urllib.parse.urljoin(base + "/", home.text)
    s, b = _dav("PROPFIND", home_url, _LIST_XML, user, pwd, depth="1")
    if s not in (200, 207):
        die(f"读取日历列表失败：HTTP {s}")
    for resp in ET.fromstring(b).findall("D:response", NS):
        disp = resp.find(".//D:displayname", NS)
        h = resp.find("D:href", NS)
        if disp is not None and h is not None and (disp.text or "").strip() == name:
            return urllib.parse.urljoin(home_url, h.text)
    if not create:
        die(f"iCloud 里没有名为「{name}」的日历")
    new_url = urllib.parse.urljoin(home_url, str(uuid.uuid4()).upper() + "/")
    body = ('<?xml version="1.0" encoding="utf-8"?><C:mkcalendar xmlns:D="DAV:" '
            'xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop>'
            f'<D:displayname>{_esc(name)}</D:displayname>'
            '<C:supported-calendar-component-set><C:comp name="VEVENT"/>'
            '</C:supported-calendar-component-set></D:prop></D:set></C:mkcalendar>')
    s, _ = _dav("MKCALENDAR", new_url, body, user, pwd)
    if s not in (200, 201):
        die(f"创建日历「{name}」失败：HTTP {s}")
    return new_url


def caldav_events(cal_url: str, user: str, pwd: str) -> dict:
    s, b = _dav("REPORT", cal_url, _REPORT_XML, user, pwd, depth="1")
    if s not in (200, 207):
        die(f"读取日历事件失败：HTTP {s}")
    out = {}
    for resp in ET.fromstring(b).findall("D:response", NS):
        h = resp.find("D:href", NS)
        data = resp.find(".//C:calendar-data", NS)
        if h is None or data is None or not data.text:
            continue
        m = re.search(r"UID:([^\r\n]+)", data.text)
        if m:
            out[m.group(1).strip()] = urllib.parse.urljoin(cal_url, h.text)
    return out


def sync_caldav(schedule: dict, keep_all: bool = False, name: str | None = None, alarm=None) -> tuple[int, int, str]:
    user, pwd, cal_name = caldav_config()
    cal_name = name or cal_name
    cal = caldav_calendar_url(user, pwd, cal_name)
    events, _ = collect_events(schedule, keep_all)
    if not events:
        die("没有要同步的班次")
    stamp = _stamp(schedule)
    existing = caldav_events(cal, user, pwd)
    puts = dels = 0
    for e in events:
        href = existing.get(e["uid"]) or urllib.parse.urljoin(
            cal, urllib.parse.quote(e["uid"], safe="@") + ".ics")
        s, msg = _dav("PUT", href, event_ics(e, stamp, alarm), user, pwd,
                      ctype="text/calendar; charset=utf-8")
        if s not in (200, 201, 204):
            die(f"写入 {e['day']} 失败：HTTP {s} {msg[:120]}")
        puts += 1
    for uid, href in existing.items():
        if uid not in {e["uid"] for e in events} and uid.endswith("@paiban2ics"):
            s, _ = _dav("DELETE", href, None, user, pwd)
            if s in (200, 204, 404):
                dels += 1
    return puts, dels, cal_name


def cmd_build(args) -> None:
    src = Path(args.schedule)
    if not src.exists():
        die(f"找不到 {src}")
    schedule = json.loads(src.read_text(encoding="utf-8"))
    kept, skipped = build_ics(schedule, Path(args.out), parse_alarm(args.alarm), args.all)
    note = f"，跳过 {skipped} 个已结束" if skipped else ""
    print(f"已生成 {args.out}：{kept} 个未结束的班次{note}", file=sys.stderr)


def cmd_sync(args) -> None:
    src = Path(args.schedule)
    if not src.exists():
        die(f"找不到 {src}")
    schedule = json.loads(src.read_text(encoding="utf-8"))
    puts, dels, name = sync_caldav(schedule, args.all, args.calendar or None, parse_alarm(args.alarm))
    note = f"，删除 {dels} 个过期" if dels else ""
    print(f"已同步 {puts} 个班次到 iCloud 日历「{name}」{note}", file=sys.stderr)


def _git_publish() -> None:
    if not (ROOT / ".git").exists():
        print("提示：不是 git 仓库，跳过推送", file=sys.stderr)
        return
    def run(*a):
        return subprocess.run(a, cwd=ROOT, capture_output=True, text=True)
    run("git", "add", "-A")
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        print("仓库无变化，跳过提交", file=sys.stderr)
        return
    r = run("git", "commit", "-m", "chore: 更新排班 " + date.today().isoformat())
    if r.returncode != 0:
        print("提交失败：" + (r.stderr or r.stdout).strip()[:200], file=sys.stderr)
        return
    r = run("git", "push", "origin", "main")
    if r.returncode == 0:
        print("已推送 GitHub", file=sys.stderr)
    else:
        print("推送失败：" + (r.stderr or r.stdout).strip()[:200], file=sys.stderr)


def cmd_publish(args) -> None:
    schedule_path = ROOT / "schedule.json"
    images = sorted(ROOT.glob("images/*.png")) + sorted(ROOT.glob("images/*.jpg"))
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if images and key:
        cmd_recognize(argparse.Namespace(images=[str(p) for p in images],
                                         out=str(schedule_path)))
    elif images:
        print("提示：未设置 DEEPSEEK_API_KEY，跳过识别", file=sys.stderr)
    if not schedule_path.exists():
        die("没有 schedule.json")
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    kept, skipped = build_ics(schedule, ROOT / "docs" / "schedule.ics", parse_alarm(args.alarm), args.all)
    note = f"（跳过 {skipped} 个已结束）" if skipped else ""
    print(f"日历：{kept} 个班次{note}", file=sys.stderr)
    if not args.no_caldav:
        puts, dels, name = sync_caldav(schedule, args.all, args.calendar or None, parse_alarm(args.alarm))
        print(f"iCloud「{name}」：写入 {puts}，删除 {dels}", file=sys.stderr)
    if not args.no_git:
        _git_publish()


def cmd_status(args) -> None:
    schedule = json.loads((ROOT / "schedule.json").read_text(encoding="utf-8"))
    events, _ = collect_events(schedule, False)
    if not events:
        print("没有未结束的班次")
        return
    for e in events:
        print(f"{e['day']}  {e['shift']}  {e['cfg']['start']}-{e['cfg']['end']}  {e['store']}")
    print(f"\n共 {len(events)} 个未结束班次")
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    print("仓库有未提交改动" if r.stdout.strip() else "仓库干净")


def cmd_update(args) -> None:
    images = sorted(ROOT.glob("images/*.png")) + sorted(ROOT.glob("images/*.jpg"))
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    schedule_path = ROOT / "schedule.json"
    if images and key:
        cmd_recognize(argparse.Namespace(images=[str(p) for p in images],
                                         out=str(schedule_path)))
    elif images:
        print("提示：未设置 DEEPSEEK_API_KEY，跳过识别，沿用已有 schedule.json", file=sys.stderr)
    if not schedule_path.exists():
        die("没有 schedule.json，无法生成")
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    kept, skipped = build_ics(schedule, ROOT / "docs" / "schedule.ics", parse_alarm(args.alarm), args.all)
    note = f"，跳过 {skipped} 个已结束" if skipped else ""
    print(f"已生成 docs/schedule.ics：{kept} 个班次{note}", file=sys.stderr)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="排班表截图 -> 日历(.ics)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("recognize", help="用多模态模型识别截图，输出 schedule.json")
    p.add_argument("images", nargs="+")
    p.add_argument("-o", "--out", default="schedule.json")
    p.set_defaults(func=cmd_recognize)

    p = sub.add_parser("build", help="由 schedule.json 生成 .ics")
    p.add_argument("schedule", nargs="?", default="schedule.json")
    p.add_argument("-o", "--out", default="docs/schedule.ics")
    p.add_argument("--alarm", default="auto", help="auto=按通勤时间，off=不提醒，或固定分钟数")
    p.add_argument("--all", action="store_true", help="保留历史班次（默认只留未结束的）")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("publish", help="识别 + 生成 + 同步 iCloud + 推送 GitHub")
    p.add_argument("--no-git", action="store_true", help="不提交推送")
    p.add_argument("--no-caldav", action="store_true", help="不同步 iCloud")
    p.add_argument("--calendar", default="")
    p.add_argument("--alarm", default="auto", help="auto=按通勤时间，off=不提醒，或固定分钟数")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("status", help="查看未结束的班次")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync-caldav", help="同步到 iCloud 日历")
    p.add_argument("schedule", nargs="?", default="schedule.json")
    p.add_argument("--calendar", default="", help="日历名称，默认读 ICLOUD_CAL")
    p.add_argument("--all", action="store_true", help="包含已结束的班次")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("update", help="识别 images/ 下所有截图并重新生成")
    p.add_argument("--alarm", default="auto", help="auto=按通勤时间，off=不提醒，或固定分钟数")
    p.add_argument("--all", action="store_true", help="保留历史班次（默认只留未结束的）")
    p.set_defaults(func=cmd_update)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
