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
| 4 | 窗口内的班次：闹钟名与响铃时间 | 标签和时间算得对不对 |
| 6 | 时钟里的全部闹钟 label | 连成一串；看不到「排班·…」= 读不到 label 或闹钟不存在 |
| 7 | 每个闹钟的 label | 能不能读到标签 |
| 8/9 | 同名打开 / 旧闹钟关掉 | 走的是哪个分支 |
| 10/11 | 已新建 / 未新建 | 最终有没有真正建出来 |
| 12 | 跑完了 | 全流程结束 |

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
| `Add Alarm` 少传 `label`、`repeatSchedule`、`ShowWhenRun` | 建出来的闹钟没名字，还会停在编辑弹窗要人手动选时间 |
| `repeats` 传 `"Never"` | 枚举里只有 sunday…saturday，传了就是非法值；不传才是"不重复" |
| 引用名和动作实际输出名不一致 | 编辑器显示「未知变量」 |
| `WFNumberValue` 写成整数 | 数字比较永远为假（见下） |
| 读实体属性的 aggrandizement 漏了 `PropertyUserInfo` | 把属性名当字面量输出（断点里显示成英文单词 `label`），条件恒假 |
| 指望 Find Alarms 的过滤条件 | iOS 26 直接无视它，永远返回全部闹钟（见下） |
| 「如果」里做文本比较（是 / 包含），或比较值放变量 | iOS 15.4 上那行参数直接变红、缺参数（见下） |

**iOS 15.4 的「如果」不做文本比较。** 2026-11-12 真机连报两次：先是「如果 是」
「如果 包含」的比较值填了变量就变红；把变量都换成固定字符串之后，
「如果 重复项目 包含 排班」**仍然**是红的。所以 15.4 上凡是「是 / 包含」这类
文本条件一律不可用 —— 不管比较值是变量还是固定值。

现在的规矩：**「如果」里只用数字比较**。任何文本判断都拆成三步 ——
`匹配文本`（正则框填变量没问题）→ `数项目`（数命中条数）→ `如果 <条数> > 0`。
主指令里三处判断（有没有同名闹钟、循环里这个闹钟是不是它、旧闹钟是不是排班的）
全是这套。`num_if()` 顺带保证数字存成字符串。

**`WFNumberValue` 必须是字符串。** shortcutkit 的 `if_()` 把数值比较的值
写成 Python 整数，plist 里是 `<integer>`；而 iOS 真实存的都是字符串
（真机样本 `"2"`、vergenzt 参考实现 `'0'`）。写成整数时 iOS 26 的条件
配不上，全判为假 —— 2026-11-11 真机踩过：明明明天有班次，
「还有多少秒 > 0」和「<= 86400」全失败，一条都挑不到。
所以生成器里用自定义的 `num_if()`：先按整数喂给 shortcutkit 过校验，
再把它落盘的参数改成字符串。

**读闹钟的名字走「整项转文本」，不要走实体 `label` 属性。** 2026-11-12 在真机
上做了四种读法的对照（体检件一次跑完）：

| 读法 | 结果 |
| --- | --- |
| 重复项目直接转文本 | **有名字** |
| 先强制转成文本类型再转文本 | **有名字** |
| 转成 `AlarmEntity` 再取 `label` 属性 | **读不出来**（iOS 26 与 15.4 都一样）|
| 转成 `AlarmEntity` 再取 `dateComponents` 属性 | **有时刻** |

所以名字用第一种（`alarm_name()` 原样返回循环项），时刻用第四种
（`alarm_time()` 拼两层 aggrandizement，再交给「格式化日期」只留时间）。
`label` 属性那条路彻底不用了，测试 `test_alarm_name_uses_plain_text_not_entity_property`
会拦（整份 plist 里出现取 `label` 的实体属性就失败）。

**不要给 Find Alarms 加过滤条件。** 2026-11-12 在 iOS 26 真机上反复验证：
`WFContentItemFilter` 被完全忽略 —— 带过滤「排班」8 个、不带过滤还是 8 个。
中间试过两轮补救都没用：给文本行补 `Values.Unit: 4`、把 descriptor 换成
`AlarmEntity` + `com.apple.mobiletimer`（真机验证能用的那份）。结论是这个动作
在 iOS 26 上只按 `WFContentItemInputParameter` 返回全部闹钟。

所以生成器现在的做法是：**取全部闹钟，先看文本、再关旧的**。

**「有没有同名闹钟」靠名字判断，不能靠实体属性。** 2026-11-12 在 15.4 真机上
踩过：靠 `label` 属性判断完全不成立，每次都当「没有同名闹钟」，于是每跑一次就
重复建一个。现在改成「把闹钟列表转成文本（`Get Text from Input`），看里面有没有
目标标签」——只依赖「把闹钟转成文字」这一件事，跨版本成立；而且判断失误的后果
是「不建」，不会像属性判断那样误判成「该建」。

- 文本里已经有目标标签 → 进循环把**名字正好等于**目标标签的那一个打开（之前
  可能被手动关过），然后 `A.EXIT` 收工。
- 文本里没有 → 先把 label 含「排班」的旧闹钟关掉，再新建，**新建完也立刻 `A.EXIT`**。

全部文本判断都不是「如果 是 <变量>」，而是
`匹配文本 → 数项目 → 如果 条数 > 0`。**15.4 的「如果」完全不做文本比较**：
连「输入是实体变量 + 比较值是固定字符串」也标红，所以一个文本条件都不留，
11 个条件全是数字比较（原因见上面的坑表）。

两次收工都是为了「只维护最近那一个闹钟」。一天里「今天还有一班 + 明天有一班」
时两条班次都落在 24 小时窗口内，如果不停手就会连建两个闹钟，后一个还把
前一个关掉；闹钟不会被删除，每天跑一遍就攒一堆关着的闹钟
（2026-11-12 用户反馈「会建很多闹钟」）。测试
`test_only_earliest_shift_is_handled` 会拦住「把收工去掉」。

测试 `test_find_alarms_has_input_and_no_filter`（不许带过滤）与
`test_loop_decides_by_label`（必须有 `A.EXIT` + contains/is 两个条件）会拦。

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

所以清单只负责描述「有哪些班次」，怎么处理完全由快捷指令在**运行时**用当前时间决定：
- 遍历 `alarms[]`，算每条 `at` 距现在还有多少秒（`获取日期之间的时间`，单位 `Total Time` = 秒）
- 跳过已经过了的（≤0）
- 跳过超过 24 小时的（>86400）：闹钟只有「时:分」没日期，
  设 07:45 就是下一个 07:45 响，目标太远会提前到错误的那天响
- **每条落在窗口内的班次各自维护自己的闹钟**（不再"只挑第一条"）

## 它做什么
1. 拉 `alarms.json`，取 `alarms[]`
2. 对每条落进 24 小时窗口的班次：
   - 目标标签 = `label` + 空格 + `time`，例如 **`排班·早班 07:45`**
   - 对时钟里的每个「排班」闹钟：
     - 名字正好等于目标标签 → 它就是这次要的，**打开它**（之前可能被关过）
     - 其它「排班」闹钟 → 关掉（`Toggle Alarm`，不删除）
   - 时钟里没有同名闹钟 → 用 `Add Alarm` 建一个

所以时钟里始终只有**一个**在用的排班闹钟，反复运行不会越攒越多。
一天通常只有一条班次进窗口，偶尔两条就各自维护各自的。

## 变量只用一处：新建闹钟的日期

iOS 26 上 `Set Variable` 写的变量，在 `Repeat` / `If` 块**里面**写、块**外面**读，
读出来是**空值**。上一版用「已完成」当标志位（循环里挑到第一条就置「是」，
循环外判断），结果循环外永远读到空 → 分支走 else → 一条班次都没处理，
而且**完全不报错**，看起来就像"没识别到排班"。

所以：**跨块状态一概不用变量**，只拿动作输出当引用；需要"记住"一个值时，
就多做一个 `Get Text` 之类的动作产出它，后面用 `sk.ref(那个动作)` 引用。
想收工时直接用「停止这个快捷指令」，而不是用变量记「已经处理过了」。
实际上现在**一个变量都没有**：连「日期先落进变量再取 `.Time`」那条也试过了，
真机上照样卡在选择器里、闹钟还没名字（见下一节），所以改成了格式化文本。

## 新建闹钟的时刻为什么是"格式化出来的 HH:mm"
`Add Alarm` 的 `dateComponents` 在 App Intent 里是 `WFDateFieldParameter`，
按 shortcutkit 的 `encoding-table.json`，它只有 `WFTextTokenString` 一种合法编码，
附件可以是 `ActionOutput` / `Variable` / 纯文本。所以"点开选择器让你手填"
不是类型写错，而是**附件里那个值 iOS 26 解析不出来**。

2026-11-12 真机连续踩了三版：
1. 直接引用「日期」动作的输出 → 卡在要求手动选日期的弹窗；
2. 先 `Set Variable` 存起来、再取变量的 `.Time` 属性 → 一样卡，且闹钟没名字；
3. 现在这版：用「格式化日期」（`WFDateFormatStyle: Custom`、`WFDateFormat: HH:mm`）
   把时刻拍成 `"07:45"` 这样的文本，再把这个**文本**交给 `dateComponents`。

理由是闹钟只认时刻（几点几分），不需要整串日期，也就绕开了 `.Time`
这个版本差异最大的属性访问器。测试 `test_alarm_time_is_formatted_hhmm_text`
和 `test_no_variables_at_all` 守着这条线。

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
- 「添加闹钟」除了参数表里的 `dateComponents`、`name`、`allowsSnooze`、`repeats`、
  `OpenWhenRun`，还必须带上 **`label`、`repeatSchedule`、`ShowWhenRun`** 这三个 ——
  它们不在 shortcutkit 的目录里，所以 `add_alarm()` 生成后直接写进参数字典。
  少了它们的后果是真机反馈的那两句：「名字没有传入具体名称」
  「创建时间还是需要用户手动选择」。`label` 和 `name` 填同一个标签；
  `repeatSchedule` 固定 `{displayString: "Never", value: 0}`；**不传 `repeats`** 才是不重复
- 「查找闹钟」必须带 `WFContentItemInputParameter`（值只能是 `Library`），
  少了真机同样报「请为此操作中的每个参数选取值，然后终止了」
