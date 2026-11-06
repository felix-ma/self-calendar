# 排班日历 · 项目约定

排班表截图 → 结构化排班 → `.ics` → iCloud 三日历 + GitHub Pages 预览。
详细用法见 `README.md`，操作流程见技能 `/paiban`。

## 命令

```bash
cd /root/workspace/paiban
python3 paiban.py status       # 看未结束班次
python3 paiban.py build        # 只生成 docs/schedule.ics
python3 paiban.py sync-caldav  # 只同步 iCloud
python3 paiban.py publish      # 识别新图 + 生成 + 同步 + 推送
```

没设 `DEEPSEEK_API_KEY` 时 `publish` 跳过识别，直接用现有 `schedule.json`。

## 硬规则

- 只输出未结束的班次，`--all` 才含历史
- 不写姓名；截图不提交（`images/` 已 ignore）
- 不加 GitHub Actions；仓库公开
- 凭据只在 `~/.config/paiban/caldav.env`（600），不进仓库、不打印
- 说明文档（README、本文件、页面文案）不写门店名、地址、坐标、距离；
  这些只放 `places.json`、`commute.json`
- 改代码后至少跑 `status` + `build`；同步前记住 iCloud 同账号 UID 唯一

## 结构

| 路径 | 作用 |
| --- | --- |
| `paiban.py` | 识别、生成、CalDAV、发布，单文件 |
| `schedule.json` | `days[]`：date / shift / store |
| `places.json` | 门店全称、地址 |
| `commute.json` | 起点、坐标、车程、通勤方式 |
| `docs/schedule.ics` | 生成结果，Pages 提供 |
| `docs/index.html` | 预览页，运行时拉 ics |

## 识别流程

新图先存 `images/`，只把新图识别到临时 JSON，合并进 `schedule.json` 后再 publish。
不要对全部旧图重跑识别，会覆盖已确认的班次。

## 提交

中文 commit，前缀 `feat:` / `fix:` / `docs:` / `chore:` / `refactor:`。
提交前 `git status` 确认没有截图、没有凭据。
