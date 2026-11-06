# 排班截图 → 日历

排班表截图交给 DeepSeek 多模态模型识别，生成 `.ics`，同步到 iCloud 日历，
再由 GitHub Pages 提供预览和订阅。仓库里不放截图、不含姓名，生成只用 Python 标准库。

```
images/*.png ──recognize──> schedule.json ──build──> docs/schedule.ics ──> Pages
                                      └────────────> iCloud（三个彩色日历）
```

## 文件

| 文件 | 作用 |
| --- | --- |
| `paiban.py` | 识别、生成、同步、发布，一个脚本 |
| `schedule.json` | 排班数据：日期、班次、门店 |
| `places.json` | 五家社康的全称和地址，写进日历地点和导航链接 |
| `commute.json` | 起点、门店坐标、车程、通勤方式 |
| `docs/schedule.ics` | 生成好的日历文件，Pages 直接提供 |
| `docs/index.html` | 日历预览页，打开时实时读取 `schedule.ics` |
| `images/` | 截图目录，已 gitignore，只留在本机 |

## 日常用法

```bash
python3 paiban.py publish     # 识别 → 生成 → 同步 iCloud → 推送 GitHub
python3 paiban.py status      # 看未结束的班次和仓库状态
```

`publish` 没设 `DEEPSEEK_API_KEY` 时会跳过识别，直接用现有的 `schedule.json`。
也可以分步执行：

```bash
export DEEPSEEK_API_KEY=sk-...
python3 paiban.py recognize images/*.png -o schedule.json
python3 paiban.py build
python3 paiban.py sync-caldav
```

## 生成规则

- 时段固定在脚本顶部 `SHIFTS`：早班 08:00–15:00、晚班 15:00–22:00、
  白班 08:00–17:00（午休写在事件备注里）
- 休息日不进日历
- 只写还没结束的班次，当天下班后自动从 `.ics` 消失；`--all` 导出全部历史
- 每张截图必须正好 7 天；第一列按周一推断年份，跨年自动进位
- 同一日期出现在多张图里时，后识别的覆盖先识别的

## 同步到 iCloud

iOS 系统日历只能按「日历」上色，不支持单个事件颜色，所以按班次拆成三个：

| 日历 | 颜色 | 班次 | 提醒示例 |
| --- | --- | --- | --- |
| 排班·早班 | 绿 `#34C759` | 08:00–15:00 | 悦兴 07:45、和风 07:30 |
| 排班·晚班 | 橙 `#FF9500` | 15:00–22:00 | 世纪 14:45 |
| 排班·白班 | 粉 `#FF2D55` | 08:00–17:00 | 泽润 07:35 |

- 第一次同步自动建日历并设色，之后只增删改事件
- UID 由「日期 + 班次 + 门店」哈希得到，稳定不变，改班次是原地覆盖
- 只动 `@paiban2ics` 结尾的事件，不碰日历里的其他事件
- iCloud 同一账号内 UID 唯一，同一个事件不要同时写进两个日历
- 三个日历要分别共享；对方通过 iCloud 共享接受后，颜色和每条事件的提醒都会带过去

凭据只放本机：

```bash
mkdir -p ~/.config/paiban
printf 'ICLOUD_USER=你的 Apple ID\nICLOUD_PASS=App 专用密码\n' > ~/.config/paiban/caldav.env
chmod 600 ~/.config/paiban/caldav.env
```

## 订阅还是导入

- **订阅**（`webcal://` 或「从网址添加」）：定期重新拉取，源里删掉的事件会自动消失
- **导入**（下载 `.ics` 再打开）：一次性复制，之后更新不会同步，重导入只会多一份

订阅地址就是 `https://felix-ma.github.io/self-calendar/schedule.ics`，
把 `https://` 换成 `webcal://` 即可；页面上的「订阅」按钮会自动转。

- Android / Google 日历：网页版「其他日历 → 从网址添加」，填上面的地址
- iPhone：订阅的日历只读，系统**不采用事件里的提醒**，也只有一个颜色；
  要在 iPhone 上分色和按通勤提醒，用 iCloud 共享的三个日历，不要用订阅

## 提醒时间

提醒量按通勤时间算，**5 km 内骑电动车，更远打车**：

- 电动车：`km / speed_kmh × 60 + extra_min`（16 km/h，+5 分钟停车）
- 打车：`OSRM 车程 × traffic_factor + extra_min`（1.8 折算高峰，+8 分钟等车）

结果取整到 5 分钟、夹在 `min_alarm` / `max_alarm` 之间，写进该事件的 `VALARM`。
远的班次提醒早、近的提醒晚；事件备注里也会写「通勤：打车 13.2 km，预留 30 分钟」。

- 起点：观澜·中航格澜郡（22.69389, 114.04109）
- 改 `commute.json` 的速度、系数或某家的 `km` / `drive_min`，重新 `publish` 生效
- 某家想固定方式：加 `"mode": "ebike"` 或 `"taxi"`（泽润就是手动设为电动车的）
- `--alarm off` 关闭提醒，`--alarm 30` 统一提前 30 分钟

## 设计取舍

- **纯标准库**：手机环境免装依赖，ICS 折行、VTIMEZONE、CalDAV 都手写
- **`.ics` 提交进仓库**：Pages 只提供静态文件；页面每次打开都带时间戳重新拉取，不受缓存影响
- **只生成未来班次**：文件小，手机日历也不会被历史占满
- **按日历分色**：iOS 不支持事件级颜色，写 `COLOR` 也不认，只能拆日历
- **提醒写进事件**：共享日历会把 `VALARM` 一起带过去；订阅日历则不会

## 隐私

仓库里只有代码、门店地址和排班数据，不含姓名；截图只留在本地 `images/`。
