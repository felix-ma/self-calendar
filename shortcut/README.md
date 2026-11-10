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

## 提前量从哪来
跟日历提醒**完全一致**，不用另配：

`paiban.py` 算通勤（5km 内电动车、更远打车）后，把结果写进事件备注第一行：

```
闹钟提前：30 分钟
```

快捷指令读到这个数字就用它；备注里没有这行时才退回默认 60 分钟。

## 它做什么
1. 找**今天**、**标题含「班」**、**非全天**的日历日程
2. 找时钟里 **label 含「排班」** 的旧闹钟，全部删除
   （Find Alarms 里先按 label 过滤，循环里再用 label 属性判一次，不会碰别的闹钟）
3. 逐个日程：读备注里的提前量 → 开始时间往回推 → 建新闹钟
   闹钟标签沿用日程标题（如「早班·门店」）

跑两次结果一样（幂等），不会堆积。

## 怎么用
1. 导入快捷指令，首次运行允许「日历」权限
2. 建议先在时钟里手动建一个叫「排班测试」的闹钟，跑一次看它是否被删掉——验证权限
3. 快捷指令 → 自动化 → 时间 → **每天 00:05** → 加动作「排班闹钟」
4. **必须关掉「运行前询问」**，否则不会自动跑
5. 低电量模式下自动化会延迟，属正常

## 改默认提前量
`build_shortcut.py` 里的 `LEAD`（默认 60）是备注缺失时的兜底值。
真正生效的是 `paiban.py` 算出来的通勤时间——想调就改 `commute.json` 里的
`traffic_factor` / `extra_min` / `drive_min`，然后重新 `build` + 同步日历。

## 注意
- 系统没有「修改闹钟」接口，只能删旧建新
- 只处理标题含「班」的日程，别的日程不会被建成闹钟
- 就寝/睡眠闹钟不受快捷指令控制
