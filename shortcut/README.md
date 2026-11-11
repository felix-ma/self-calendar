# 排班闹钟 · iOS 快捷指令

## 文件
- `排班闹钟.shortcut` —— 主指令，已签名，每天自动跑
- `排班闹钟·更新.shortcut` —— 配套的一键更新指令（打开系统导入页，点「替换」）
- `build_shortcut.py` —— 生成器，`main` / `updater` 两种
- `sign.py` —— 签名脚本，把 plist 变成可导入的 `.shortcut`

## 重新生成
```bash
python3 shortcut/build_shortcut.py /tmp/main.plist main
python3 shortcut/sign.py /tmp/main.plist shortcut/排班闹钟.shortcut
cp shortcut/排班闹钟.shortcut docs/paiban-alarm.shortcut        # 页面按钮用这份

python3 shortcut/build_shortcut.py /tmp/upd.plist updater
python3 shortcut/sign.py /tmp/upd.plist "shortcut/排班闹钟·更新.shortcut"
cp "shortcut/排班闹钟·更新.shortcut" docs/paiban-alarm-update.shortcut
```

**改了主指令的动作或判断，就把 `build_shortcut.py` 的 `VERSION`
和 `paiban.py` 的 `SHORTCUT_VERSION` 一起 +1**（两边必须一致），
否则用户手机上不会收到更新提示。只改班次/时间/提前量则不用动版本。

## 导入方式
1. **页面按钮**：打开 <https://felix-ma.github.io/self-calendar/>，
   点「下载闹钟快捷指令」，得到 `paiban-alarm.shortcut`；
   在 Safari 的下载项里点开它，就会加入快捷指令 App
2. **AirDrop**：把 `.shortcut` 传到 iPhone
3. **文件 App**：下载后点开

## 数据从哪来
**不读日历、不读备注**，直接请求线上清单：

```
https://felix-ma.github.io/self-calendar/alarms.json
```

这是 `paiban.py build` 生成的静态文件，字段固定，本机可测。
之前试过从事件备注里用正则抠「预留：N 分钟」，但 iOS 各版本对备注的读取
不一致，经常取不到数，所以改成拉 JSON。

清单里跟快捷指令有关的是两个字段：

| 字段 | 含义 |
| --- | --- |
| `next` | 下一次真正要建的闹钟（含 `at`/`time`/`label`/`shift`/`lead`） |
| `due` | `next` 且在 **24 小时内**，可以直接建；否则为 `null` |

## 「今天过了就看明天」的判断在哪
在 `paiban.py` 里（本机能测），不在快捷指令里：
- `next` 只取还没到的班次，今天的班次过了就自动落到明天
- 明天没排班，`next` 就落到后天或更后面；全都没排班就是 `null`
- 闹钟只有「时:分」没有日期，所以只有距现在 ≤24 小时才建（`due`），
  否则会把明天的闹钟提前到今天响。**超过 24 小时的不建，留给明天的自动化**

## 它做什么
0. 先比对 `alarms.json` 里的 `shortcut_version` 与自身内置版本；
   不一致 → 弹一条通知「有新版本，方便时运行『排班闹钟·更新』」，
   **不打开任何页面**（自动化时没人看着，打开只会卡住），随后照常继续跑
1. 拉 `alarms.json`，取 `due`；为空 → 弹一条通知「没有需要新建的闹钟」，结束
2. 目标标签 = `due.label` + 空格 + `due.time`，例如 **`排班·早班 07:45`**
3. 取时钟里**所有**闹钟，逐个看 label，含「排班」的：
   - 名字正好等于目标标签 → 它就是这次要的，**打开它**（之前可能被关过）
   - 其它「排班」闹钟 → 关掉（`Toggle Alarm`，不删除）
4. 循环结束仍没找到目标标签 → 用 `Add Alarm` 建一个

所以同一个班次同一个时刻，始终只维护**一个**闹钟，反复运行不会越攒越多。

## 为什么标签里要带时间
可用的只有「查找 / 开关 / 添加」三个动作，改不了已存在闹钟的时间，
换班次或换时间只能关旧建新。把时间编进标签后，判据变成「有没有同名闹钟」，
重复运行是幂等的。手工删掉那个闹钟也没关系，下次会自动重建。

## 为什么不用「更新闹钟」
`Update Alarm`（`com.apple.clock.UpdateAlarmIntent`）是 **iOS 27 才有的动作**。
iOS 26 导入这个 `.shortcut` 时，该动作会被**静默丢弃**，但后续步骤照常执行，
于是出现「什么都不发生、也不报错」——2026-11-11 在 iOS 26 上真机踩过。
现在只用 iOS 26 就有的 `com.apple.mobiletimer-framework` 三个动作。

## 为什么不用「删除」
删除必须弹确认框让用户点；关闭（`Toggle`）不需要，静默生效。

## 更新快捷指令
iOS **不允许**快捷指令静默覆盖自己，所以「点一下替换」这步去不掉。
能做的是把前面所有步骤（找下载地址、下载、点开文件）省掉：

1. 运行「排班闹钟·更新」
2. 系统弹出导入页，点「替换」

主指令发现版本不一致时会提醒你去点；**自动化运行到这一步只弹提醒、跳过**，
不会因为没人点而卡住或失败，闹钟照旧建。

日常真正会变的只有班次数据（写在 `alarms.json` 里，我推一次就生效），
那种情况**不需要**任何重装；只有快捷指令本身的逻辑改了才需要更新。

## 怎么用
1. 导入快捷指令；它要联网，不需要日历权限
2. 快捷指令 → 自动化 → 时间 → **每天 00:05** → 加动作「排班闹钟」
3. **必须关掉「运行前询问」**，否则不会自动跑
4. 低电量模式下自动化会延迟，属正常

## 调提前量
改 `commute.json` 的 `traffic_factor` / `extra_min` / `drive_min`，
然后 `python3 paiban.py build` + 同步日历 + 推送到 Pages。
闹钟和日历提醒（VALARM）用同一个数，所以两者**同一时刻**响：都是「该出发了」。

## 注意
- 别把主指令改名为别的，`排班闹钟·更新` 里的导入地址靠名字找
- 就寝/睡眠闹钟不受快捷指令控制
- `alarms.json` 必须已经推送，快捷指令才拿得到最新数据
- 时钟里旧的「排班…」闹钟会在第一次运行时被关掉（不删），之后保持干净
