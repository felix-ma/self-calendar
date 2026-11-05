# 排班截图 → 手机日历

把排班表截图交给 DeepSeek 多模态模型识别，生成可以直接导入手机日历的 `.ics`。
识别在本机跑，仓库里不放截图、不含姓名；生成只用 Python 标准库。

- 识别：`images/` 里的截图（仅本地，不入库）→ `schedule.json`
- 生成：`schedule.json` → `docs/schedule.ics`
- 只保留早班 / 晚班 / 白班，休息日不进日历
- 只写还没结束的班次，历史排班自动跳过

## 结构

```
paiban.py              识别 + 生成
places.json            各社康的全称 + 地址（用于导航）
schedule.json          排班数据（日期 / 班次 / 门店，不含姓名）
docs/schedule.ics      生成好的日历文件
docs/index.html        手机导入说明页
images/                截图目录，仅本地使用（已 gitignore）
```

## 本地用

```bash
export DEEPSEEK_API_KEY=sk-...       # DeepSeek API Key

# 1. 识别（截图放 images/）
python3 paiban.py recognize images/*.png -o schedule.json

# 2. 生成
python3 paiban.py build

# 或者一步到位
python3 paiban.py update
```

`update` 会读取 `images/` 下所有图片：有 Key 就重新识别，没有 Key 就沿用
`schedule.json` 直接生成。生成完 `git push` 即可。

## 一条命令

```bash
python3 paiban.py publish
```

依次完成：识别 `images/` → 生成 `docs/schedule.ics` → 同步 iCloud → 提交推送 GitHub。
可加 `--no-caldav` / `--no-git` / `--all`。

```bash
python3 paiban.py status     # 看未结束的班次和仓库状态
```

## 只保留未来的班次

生成时按当前时间过滤：已经结束的班次不写进 `.ics`，所以文件一直很小。
今天还没下班的班次会保留，下班后自动消失。

想导出完整历史（含已结束的）：

```bash
python3 paiban.py build --all
```

## 导入还是订阅？

- **导入**（下载 .ics 再点开）：一次性复制。之后更新了，手机不会变；
  重新导入也不会删掉旧事件，只会多出一份。
- **订阅**（webcal:// 或「从网址添加」）：定期重新拉取。源里删掉的事件会自动从
  手机日历消失，新事件自动出现。想在更新后自动同步，必须用这种方式。

订阅地址就是同一个 `schedule.ics`，把 `https://` 换成 `webcal://` 即可。

- **iPhone**：Safari 打开 `webcal://…/schedule.ics` → 订阅 → 设置 → 日历 →
  账户 → 抓取新数据，可调刷新频率
- **Android / Google 日历**：网页版 calendar.google.com → 其他日历 → 从网址添加；
  手机 App 会自动同步这个订阅日历（只读）
- 订阅的刷新有延迟：Google 几小时，iOS 按上面设置
- 订阅链接必须公开可访问

## 同步到 iCloud（CalDAV）

除了生成 `.ics`，也可以直接写进 iCloud 日历。凭据只放本机，不进仓库：

```bash
mkdir -p ~/.config/paiban
cat > ~/.config/paiban/caldav.env <<'EOF'
ICLOUD_USER=你的 Apple ID
ICLOUD_PASS=App 专用密码
ICLOUD_CAL=排班表
EOF
chmod 600 ~/.config/paiban/caldav.env

python3 paiban.py sync-caldav
```

同步按 UID 增删改：新班次写入、改班次覆盖、过期的删除；
只动 UID 以 `@paiban2ics` 结尾的事件，不会碰日历里别的事件。
默认只同步未结束的班次，加 `--all` 可连历史一起写。

## 地址与导航

`places.json` 里是各社康的全称和地址，生成时会写进日历的 `LOCATION`，
并附一条高德搜索链接（`URL`）。手机上点开事件 → 点地点，就能直接导航。

地址来自公开信息，如与实际不符，改 `places.json` 后重新 `build` 即可。

## 改班次时段

时段写在 `paiban.py` 顶部的 `SHIFTS` 里：

```python
"早班": {"start": "08:00", "end": "15:00"},
"晚班": {"start": "15:00", "end": "22:00"},
"白班": {"start": "08:00", "end": "17:00", "note": "8:00-12:00、14:00-17:00"},
```

白班在日历里显示为一整段 08:00–17:00，午休写在事件备注里。

## 隐私

仓库里只有代码、门店地址和排班数据，不含姓名；排班截图只留在本地。
