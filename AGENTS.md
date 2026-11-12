# 排班日历 · 项目约定

排班表截图 → 结构化排班 → `.ics` → iCloud 三日历 + GitHub Pages 预览。
仓库：<https://github.com/felix-ma/self-calendar>

## 命令

```bash
cd /root/workspace/paiban
python3 paiban.py status        # 未结束班次 + 仓库状态
python3 paiban.py build         # 由 schedule.json 生成 docs/schedule.ics
python3 paiban.py sync-caldav   # 只同步 iCloud
python3 paiban.py update        # 识别 images/ 全部 + 生成 ics
python3 paiban.py publish       # 识别 + 生成 + 同步 + 提交推送
python3 paiban.py recognize images/a.jpg -o /tmp/a.json   # 只识别指定图
```

通用参数：`--all`（含历史）、`--alarm auto|off|N`（提醒），
`publish` 另有 `--no-git` / `--no-caldav`。
没设 `DEEPSEEK_API_KEY` 时 `publish` / `update` 跳过识别，沿用现有 `schedule.json`。

## 凭据（不打印、不入库）

- DeepSeek：`~/.dsh/.credentials.yaml` 的 `refs.DEEPSEEK_API_KEY`
- iCloud：`~/.config/paiban/caldav.env`（`ICLOUD_USER` / `ICLOUD_PASS` 应用专用密码），权限 600

取 Key 时不要回显。该辅助命令用到 `PyYAML`，须用项目内 venv 的 python（见「依赖」）：

```bash
export DEEPSEEK_API_KEY="$($PWD/.venv/bin/python -c "import yaml;print(yaml.safe_load(open('/root/.dsh/.credentials.yaml'))['refs']['DEEPSEEK_API_KEY'])")"
```

## 收到截图后的流程

1. **落盘**：附件复制到 `images/`，命名 `排班表_YYYY-MM-DD_MM-DD.jpg`。`images/` 已 gitignore，绝不 `git add -f`。
2. **识别**：先只识别新图到临时文件，再合并进 `schedule.json`。
3. **注意**：`publish` / `update` 会把 `images/` 下**全部**截图重识别并整体重写 `schedule.json`，老图读错会覆盖已确认数据；不想冒险就**不要导出 Key** 直接 `publish`，让它沿用现有数据。
4. **发布**：导出 Key 时 `publish` 一条命令跑完；也可拆成 `build` → `sync-caldav` → 手动提交推送。
5. **核对**：`status` 看班次；线上 `schedule.ics` 里数 `BEGIN:VEVENT`。
6. **汇报**：列出未来班次的日期、班次、门店；给页面和订阅链接；说明跳过了多少历史班次。

## 硬规则

- 只输出**未结束**的班次；`--all` 才含历史。
- 不写姓名；不提交截图；不加 GitHub Actions。
- 说明文档（README、本文件、页面文案）不写门店名、地址、坐标、距离；
  这些只放 `places.json`、`commute.json`。
- iCloud 同步按 UID 增删改，只动 UID 以 `@paiban2ics` 结尾的事件。
- **绝不 DELETE 日历集合**，只删事件资源；只有用户明确要求、且确认该日历为空、URL 精确匹配那一个集合时才可以删。
- 批量删除前先用 REPORT 把事件导出到 `~/.local/share/paiban/` 备份。
- 用户手动拖动过的事件，重新同步会覆盖回脚本时间——这是预期行为。
- 改代码后至少跑 `status` + `build`；同步多次应幂等（写 0 删 0）。

## 结构

| 路径 | 作用 |
| --- | --- |
| `paiban.py` | 识别、生成、CalDAV、发布，单文件 |
| `schedule.json` | `days[]`：date / shift / store |
| `places.json` | 门店全称、地址 |
| `commute.json` | 起点、坐标、车程、通勤方式 |
| `docs/schedule.ics` | 生成结果，Pages 提供 |
| `docs/index.html` | 预览页，运行时拉 ics |

## 输出

- 订阅源：<https://felix-ma.github.io/self-calendar/schedule.ics>
- 页面：<https://felix-ma.github.io/self-calendar/>（月历 + 订阅按钮）
- iCloud 三个日历：`排班·早班`（绿）/ `排班·晚班`（橙）/ `排班·白班`（粉）
- 共享给别人时三个日历要**分别共享**；共享后 `VALARM` 会一起带过去，提醒不会退化
- `docs/alarms.json`：全部未结束班次的闹钟清单（`at`/`time`/`start`/`shift`/`label`/`commute`/`lead`），
  `--alarm-days N` 可只看未来 N 天，默认 0 = 不截断。
  **只有 `alarms[]` 全清单，没有 `due`/`next`**：这份文件是发布时的快照，可能几天不更新，
  服务器预先算好「下一次该建哪个」会过期（晚上再跑就漏了当天班次，真机踩过）。
  「挑哪一条」必须由快捷指令在运行时用当前时间决定，别把它搬回 Python
- iOS 快捷指令（`shortcut/`）：GET 线上 `alarms.json` 取 `alarms[]`，遍历并用
  「获取日期之间的时间」筛出 `at` 未到、且 ≤24 小时（86400 秒）的班次，
  **对每一条符合的班次各自维护自己的闹钟**（不是只挑第一条）：目标标签 = `label + 空格 + time`
  （如 `排班·早班 07:45`），扫时钟里全部闹钟：同名的那一个打开、其余含「排班」的关掉、
  都没有才 `Add Alarm` 新建。所以同一班次同时刻只维护一个闹钟，反复运行幂等。
  **绝对不要用变量（Set Variable / Add to Variable）** —— 2026-11-12 真机实测：
  iOS 26 上块内写的变量块外读出来是空值，旧的「已完成」标志一直读到空，
  导致循环体整段被跳过、一条班次都挑不到，而且完全不报错。
  只拿**动作输出**当引用（动作输出跨块可读）。需要「记住」一个值时，
  用 `Get Text` 之类的动作产出，再用 `sk.ref(那个动作)` 引用。
  **不要用 `Update Alarm`**：它是 iOS 27 才有的动作，iOS 26 导入时被静默丢弃，
  会出现「什么都没发生也不报错」（真机踩过）；只用 `com.apple.mobiletimer-framework`
  的 Find / Toggle / Add 三个动作。
  **不要用 Delete**：删除必须弹窗要用户确认，Toggle 不需要。已签名文件随仓库提交
- **只有一个快捷指令**，不做版本检查、不做「更新器」指令（用户明确要求删掉）：
  iOS 不允许快捷指令静默覆盖自己，任何"自动更新"都会卡在导入页。
  逻辑变了就让用户从页面重新下载一次，数据变化不用重装
- 页面按钮：`docs/paiban-alarm.shortcut` 是下载副本（直接下载，不用 `shortcuts://` 前缀）；
  每次重新签名后要 `cp shortcut/排班闹钟.shortcut docs/paiban-alarm.shortcut`
- 快捷指令生成器 `shortcut/build_shortcut.py` 用 **shortcutkit** 写（动作目录会校验参数名，
  写错当场 `ValueError: unknown parameter(s)`），**必须 `.venv/bin/python` 跑**；
  签名用 `shortcut/sign.py`（远程 `playground.cherrilang.org`，产物首字节 `AEA1`）。
  两条命令见 `shortcut/README.md`「重新生成」
- 真机排查用 **`--debug` 分步弹窗版**（`docs/paiban-alarm-debug.shortcut`）：
  每个关键步骤弹一个「显示提醒」，点「好」继续、点「取消」停下，
  直接把当时的 JSON 原文 / 条数 / 距今秒数 / 标签显示出来。
  **不要把日志攒进变量再一次性显示** —— 2026-11-12 真机踩过，变量一直是空白
  （同一条变量不可用的坑）；弹窗内容也必须用 `merge()` 自己拼，
  不能用 `sk.text()` 套 `sk.text()`

## 提醒与通勤

- 每个事件单独写 `VALARM`：5 km 内骑电动车（`km / speed_kmh × 60 + extra_min`），
  更远打车（`OSRM 车程 × traffic_factor + extra_min`），取整 5 分钟，夹在 `min_alarm` / `max_alarm` 之间。
- 起点在 `commute.json.origin`，换起点改坐标后用 OSRM 重算车程（本机可访问 `router.project-osrm.org`）。
- 某家可加 `"mode": "ebike"|"taxi"` 覆盖 5 km 规则；`approx: true` 表示坐标是近似值，用户纠正时以配置为准。
- 用户说「提醒太早/太晚」：调 `traffic_factor` / `extra_min`，或该店的 `drive_min`，再发布。
- `--alarm off` 关闭提醒，`--alarm N` 统一提前 N 分钟。

## 常见问题

- **识别天数不对**：脚本自动重试一次；仍失败就看报错里的原始 JSON，多半是截图不完整。
- **姓名认错**：不影响结果，姓名不写进任何文件。
- **iCloud 日历被删**：`sync-caldav` 会按三个固定名字自动新建并设色。
- 旧「排班表」日历已删除，不要往这个名字里写事件。
- 「家庭」是 Apple 家庭共享自动生成的日历，DELETE 后 iCloud 会立刻用新 ID 重建；只能在日历 App 里取消勾选隐藏，不要反复删。
- **iOS 没有颜色**：系统日历不支持单事件颜色，只能按日历拆；三个日历分别共享后对方也能看到颜色。
- **iOS 提醒不按事件走**：多半是加了网页订阅日历（webcal），iOS 对订阅日历不采用 `VALARM`；改用 iCloud 共享日历。
- **Pages 没更新**：push 后等 30–60 秒；页面每次打开实时读 ics。
- **推送失败**：检查 SSH 部署密钥（`ssh -T git@github.com`）和远端 `origin`。
- **时区**：事件按北京时间生成，`DTSTART` 带 `TZID=Asia/Shanghai`。

## 测试

```bash
.venv/bin/python -m unittest discover -s shortcut -v   # 生成器回归测试
```

测的是 `shortcut/build_shortcut.py` 产出的 plist 结构：根键、块配对、
引用名与输出名一致、`WFNumberValue` 必须是字符串、Add Alarm 不带
`label`/`repeatSchedule`、descriptor 是 `com.apple.clock`、24 小时窗口
两个数值条件、挑到就停手、没挑到只提示。

**改生成器必须跑它**，每个用例的 docstring 写着它对应哪次真机事故。
它拦不住 iOS 的运行时语义（条件在真机上算成真还是假、某动作在某版本
会不会被静默丢弃），那些只能真机验证。

## 依赖

- 主程序 `paiban.py` **只用 Python 标准库**（3.10+），系统 `python3` 直接跑即可；
  识别走 HTTP 接口，不需要 OCR、不需要 GitHub Actions。
- 两个第三方包：`PyYAML`（「凭据」一节取 `DEEPSEEK_API_KEY` 用）、
  `shortcutkit`（`shortcut/build_shortcut.py` 生成快捷指令用，动作目录能当场校验参数名）。
  **`build_shortcut.py` 必须用 `.venv/bin/python` 跑**，系统 python3 里没有 shortcutkit。
- 运行时 Linux 环境常被重置、系统装过的包会被清空，因此依赖放进**项目内 venv**，
  不污染系统 Python、可一键重建：

  | 路径 | 作用 |
  | --- | --- |
  | `requirements.txt` | 依赖声明 `PyYAML>=6.0`、`shortcutkit>=3.2`（随仓库提交） |
  | `.venv/` | 项目内虚拟环境，已 gitignore，**不入库** |

  环境重置后重建（项目根目录）：

  ```bash
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  ```

  用 venv 跑（推荐，取 Key 与生成快捷指令都依赖它）：`.venv/bin/python paiban.py status`；
  或 `source .venv/bin/activate` 激活后照常写 `python3`。

- 主程序**不依赖 `.venv`**：删掉它，系统 `python3 paiban.py` 仍能生成、同步、发布；
  只有取 Key 辅助命令会因缺 PyYAML 报 `No module named 'yaml'`，按上面重建即可。
