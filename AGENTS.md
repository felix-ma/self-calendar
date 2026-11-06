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

取 Key 时不要回显：

```bash
export DEEPSEEK_API_KEY="$(python3 -c "import yaml;print(yaml.safe_load(open('/root/.dsh/.credentials.yaml'))['refs']['DEEPSEEK_API_KEY'])")"
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

## 依赖

只用 Python 标准库；识别走 HTTP 接口。不需要 OCR、不需要 GitHub Actions。
