# 排班闹钟 · iOS 快捷指令

## 文件
- `排班闹钟.shortcut` —— 已签名，可直接导入 iPhone
- `build_shortcut.py` —— 生成器（XML plist），改了逻辑后重新生成
- `sign.py` —— 签名脚本，把 plist 变成可导入的 `.shortcut`

## 重新生成
```bash
python3 shortcut/build_shortcut.py /tmp/a.plist
python3 shortcut/sign.py /tmp/a.plist shortcut/排班闹钟.shortcut
cp shortcut/排班闹钟.shortcut docs/paiban-alarm.shortcut   # 页面按钮用这份
```

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
| `next` | 下一次真正要建的闹钟（含 `at`/`label`/`shift`/`lead`） |
| `due` | `next` 且在 **24 小时内**，可以直接建；否则为 `null` |

## 「今天过了就看明天」的判断在哪
在 `paiban.py` 里（本机能测），不在快捷指令里：
- `next` 只取还没到的班次，今天的班次过了就自动落到明天
- 明天没排班，`next` 就落到后天或更后面；全都没排班就是 `null`
- 闹钟只有「时:分」没有日期，所以只有距现在 ≤24 小时才建（`due`），
  否则会把明天的闹钟提前到今天响。**超过 24 小时的不建，留给明天的自动化**

## 它做什么
1. 拉 `alarms.json`
2. 取 `due`；为空 → 弹一条通知「没有需要新建的闹钟」，结束
3. 取时钟里**所有**闹钟，逐个看 label，含「排班」的**关掉**
4. 按 `due.at` 建新闹钟，名字用 `due.label`（如「排班·早班」）

## 为什么用「关掉」而不是「删除」
删除要弹窗让用户确认哪个闹钟；关掉不需要，静默生效。
旧闹钟关了不影响新闹钟，不吵人也留着记录。

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
- 系统没有「修改闹钟时间」的接口（`Update Alarm` 只在 macOS 上可用），
  所以做法是「关旧 + 建新」
- 就寝/睡眠闹钟不受快捷指令控制
- `alarms.json` 必须已经推送，快捷指令才拿得到最新数据
