# 排班闹钟 · iOS 快捷指令

## 文件
- `排班闹钟.shortcut` —— 已签名，每天自动跑
- `build_shortcut.py` —— 生成器（用 [shortcutkit](https://github.com/frontboat/shortcutkit)）
- `sign.py` —— 签名脚本，把 plist 变成可导入的 `.shortcut`

## 重新生成
**必须用 `.venv/bin/python`**，系统 python3 里没装 shortcutkit：

```bash
.venv/bin/python shortcut/build_shortcut.py /tmp/main.plist
.venv/bin/python shortcut/sign.py /tmp/main.plist shortcut/排班闹钟.shortcut
cp shortcut/排班闹钟.shortcut docs/paiban-alarm.shortcut   # 页面按钮用这份
```

缺依赖时先装（项目根目录）：

```bash
.venv/bin/python -m pip install -r requirements.txt   # 含 shortcutkit>=3.2
```

## 诊断版（真机排查用）

主指令出问题、又看不出卡在哪时，用分步弹窗的诊断版：

```bash
.venv/bin/python shortcut/build_shortcut.py /tmp/debug.plist --debug
.venv/bin/python shortcut/sign.py /tmp/debug.plist "shortcut/排班闹钟·诊断.shortcut"
cp "shortcut/排班闹钟·诊断.shortcut" docs/paiban-alarm-debug.shortcut
```

它在每个关键步骤弹一个「显示提醒」，把**那一刻的真实值**直接显示出来：
点「好」继续下一步，点「取消」就停在这里。不用把日志攒进变量
（2026-11-12 踩过：日志变量在真机上一直是空白），也能一眼看出是哪一步的值不对。

断点顺序：

| # | 弹窗内容 | 看什么 |
| --- | --- | --- |
| 1 | 解析出的 JSON 原文 | 网络和解析是否正常（先确认数据源本身对不对） |
| 2 | `alarms[]` 条数 | 0 = 清单是空的 |
| 3 | 每条的 `at` 与「距今多少秒」 | 负数 = 已过；> 86400 = 超过一天 |
| 4 | 循环结束后「已完成」 | 是 = 挑到了；否 = 一条都不符合条件 |
| 5 | 挑中的闹钟名与响铃时间 | 标签和时间算得对不对 |
| 6 | 时钟里闹钟数量（带过滤/不带过滤） | 两个数不同 = 过滤有效；都是 0 = 读不到闹钟；都等于 N = 过滤被忽略 |
| 7 | 每个闹钟的 label | 能不能读到标签 |
| 8/9 | 同名打开 / 旧闹钟关掉 | 走的是哪个分支 |
| 10/11 | 已新建 / 未新建 | 最终有没有真正建出来 |
| 12 | 没挑到班次 | 走到 else 分支 |
| 13 | 跑完了 | 全流程结束 |

**弹窗内容要自己拼接，不能把 `sk.text()` 的结果再塞给 `sk.text()`**：
`sk.text()` 只认 `WFTextTokenAttachment`，遇到已经是 `WFTextTokenString`
的片段会走 `str()`，弹出来是 `{'WFSerializationType': 'WFTextTokenString', ...}`
一长串（2026-11-11/12 踩了三次）。生成器里统一用 `merge()` 合并
`string` 与 `attachmentsByRange`，按 UTF-16 偏移重新编号；测试
`test_no_stringified_dicts_anywhere` 会拦。

正常版不插这些动作（`--debug` 不加就一个都不插），免得每天跑多做几十步。

## 为什么用 shortcutkit 生成
它把 Shortcuts 引擎里的**动作目录**（449 个内置动作 + 1265 个 Apple 应用动作）
做成了带类型校验的 Python API。写错参数名不是"生成一个坏文件"，而是当场抛
`ValueError: ...: unknown parameter(s) ['label']`。

之前手写 plist 时踩的坑它都能拦住：

| 坑 | 后果 |
| --- | --- |
| `Add Alarm` 传了 `label`、`repeatSchedule` | 真机报「请为此操作中的每个参数选取值，然后终止了」 |
| `repeats` 传 `"Never"` | 枚举里只有 sunday…saturday，传了就是非法值；不传才是"不重复" |
| descriptor 的 `BundleIdentifier` 写成 `com.apple.mobiletimer` | 目录里三个闹钟动作都是 `com.apple.clock` |
| 引用名和动作实际输出名不一致 | 编辑器显示「未知变量」 |
| `WFNumberValue` 写成整数 | 数字比较永远为假（见下） |

**`WFNumberValue` 必须是字符串。** shortcutkit 的 `if_()` 把数值比较的值
写成 Python 整数，plist 里是 `<integer>`；而 iOS 真实存的都是字符串
（真机样本 `"2"`、vergenzt 参考实现 `'0'`）。写成整数时 iOS 26 的条件
配不上，全判为假 —— 2026-11-11 真机踩过：明明明天有班次，
「还有多少秒 > 0」和「<= 86400」全失败，一条都挑不到。
所以生成器里用自定义的 `num_if()`：先按整数喂给 shortcutkit 过校验，
再把它落盘的参数改成字符串。

**签名仍走 `playground.cherrilang.org`**：shortcutkit 的 `sign()` 是 `subprocess`
调 macOS 的 `shortcuts sign` CLI，Linux 上跑不了；`sign.py` 改用远程签名服务，
解出来的文件首字节是 `AEA1`，iOS 可以导入。

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

清单里只有 `alarms[]` 这一个全清单，没有「下一次该建哪个」的字段。
每条包含 `at`/`time`/`start`/`shift`/`label`/`commute`/`lead`。

## 「挑哪一条」为什么在手机上算
因为 `alarms.json` 是**发布时的快照**，可能一整天不更新、也可能好几天不更新。
如果服务器把「今天该建哪个」算死（早先试过 `due` 字段），
到了晚上再运行就是过期数据 —— 2026-11-11 真机踩过：当天明明有白班却不建闹钟。

所以清单只负责描述「有哪些班次」，选哪一条完全由快捷指令在**运行时**用当前时间决定：
- 遍历 `alarms[]`，算每条 `at` 距现在还有多少秒（`获取日期之间的时间`，单位 `Total Time` = 秒）
- 跳过已经过了的（≤0）
- 跳过超过 24 小时的（>86400）：闹钟只有「时:分」没日期，
  设 07:45 就是下一个 07:45 响，目标太远会提前到错误的那天响
- 取第一条剩下的

## 它做什么
1. 拉 `alarms.json`，取 `alarms[]`，按上面规则挑一条；挑不到 → 弹通知「本次不建」，结束
2. 目标标签 = `label` + 空格 + `time`，例如 **`排班·早班 07:45`**
3. 取时钟里所有闹钟，逐个看 label，含「排班」的：
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

## 只保留一个指令
不做版本检查、不做配套的「更新器」指令：
- iOS **不允许**快捷指令静默覆盖自己，任何"自动更新"最后都要用户点一次「替换」
- 用一个只负责打开导入页的指令，跟直接在页面上点下载没区别，反而多一个文件要维护

所以逻辑改了就让用户从页面重新下载一次；**数据变化不用重装**
（班次、时间、提前量都在 `alarms.json` 里，推一次就生效）。

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
- 就寝/睡眠闹钟不受快捷指令控制
- `alarms.json` 必须已经推送，快捷指令才拿得到最新数据
- 时钟里旧的「排班…」闹钟会在第一次运行时被关掉（不删），之后保持干净
- 「添加闹钟」真正可用的参数只有 `dateComponents`、`name`、`allowsSnooze`、`repeats`、`OpenWhenRun`
  （**没有 `label`**，闹钟名就是 `name`；**不传 `repeats`** 才是不重复）
- 「查找闹钟」必须带 `WFContentItemInputParameter`（值只能是 `Library`），
  少了真机同样报「请为此操作中的每个参数选取值，然后终止了」
