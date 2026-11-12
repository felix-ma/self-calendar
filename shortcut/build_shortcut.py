#!/usr/bin/env python3
"""生成「排班闹钟」快捷指令（XML plist → 交给 playground 签名）。

用 shortcutkit（https://github.com/frontboat/shortcutkit）构建：
它的动作目录直接取自 Shortcuts 引擎（449 个内置动作 + 1265 个应用动作），
写错参数名会当场抛 ValueError，不用再靠真机试错。

用法（本脚本依赖 shortcutkit，必须用项目 venv 的 python）：
    .venv/bin/python shortcut/build_shortcut.py /tmp/main.plist
    .venv/bin/python shortcut/sign.py /tmp/main.plist "shortcut/排班闹钟.shortcut"

缺依赖时：.venv/bin/python -m pip install -r requirements.txt

流程（不读日历、不读备注，直接请求线上 alarms.json）：
  1. 拉 https://felix-ma.github.io/self-calendar/alarms.json，取 alarms[] 全清单
  2. 用「当前时间」逐条比较，挑出**最早**一条满足下面两条的：
     · at 还没到（已过的班次跳过）
     · 距现在不超过 24 小时（闹钟没有日期，超过一天会响错日子）
     —— 这个选择必须在运行时做。alarms.json 是发布时的快照，
        服务器先算好「今天该建哪个」会过期：晚上再跑就漏了当天的班次。
     处理完这一条就「停止快捷指令」，不再动后面的班次 ——
     否则一天里两条班次都落在窗口内（今天还有一班 + 明天有一班）时
     会连建两个闹钟，旧的只是被关掉、不删除，越攒越多。
  3. 一条都没挑到 → 只提示一句，不建
  4. 挑到了 → 目标标签 = 「label + 空格 + time」，例如「排班·早班 07:45」
     · 时钟里已有同名闹钟 → 打开它，不新建
     · 没有同名闹钟     → 把其它「排班」旧闹钟关掉，再按 at 新建一个

为什么标签里带时间：
  只有「查找 / 开关 / 添加」三个动作可用，改不了闹钟时间，只能关旧建新。
  把时间编进标签后，同一班次同一时刻的闹钟只会存在一个，不会越攒越多。

为什么不用 Update Alarm（原地改时间）：
  它是 iOS 27 才有的动作。iOS 26 导入时该动作会被静默丢弃，
  结果「什么都没发生、也不报错」—— 2026-11-11 真机踩过。

为什么不用 Delete：
  删除必须弹确认框要用户点；关闭（Toggle）不需要，静默生效。

为什么全程不用变量，却留了一个 Set Variable：
  iOS 26 上 Set Variable 在 Repeat/If 块里写、块外读会得到**空值**，
  所以「已完成」这类跨块状态一概不能用（2026-11-12 真机踩过：标志读到空，
  循环体整段被跳过，还完全不报错）。但新建闹钟的 dateComponents 又必须经由
  变量取 .Time —— 直接塞日期动作的输出，iOS 会弹「请选日期」并卡死。
  因此只保留 WAKE_VAR 这一个变量，写和读都紧挨着「添加闹钟」同一块内。

shortcutkit 目录查出来、之前手写 plist 写错的几处（别再改回去）：
  · MTCreateAlarmIntent 的真实参数只有 dateComponents / name / repeats /
    allowsSnooze / OpenWhenRun，**没有 label、没有 repeatSchedule**。
    以前多传的这两个无效键，就是真机报「请为此操作中的每个参数选取值」的元凶。
  · repeats 的可选值只有 sunday..saturday，没有「永不」→ 不重复就**整个不传**。
  · 三个闹钟动作的 AppIntentDescriptor 要用 com.apple.mobiletimer（不是目录里
    写的 com.apple.clock），AppIntentIdentifier 分别是 CreateAlarmIntent /
    AlarmEntity / ToggleAlarmIntent；Find Alarms 还必须用 find_alarms() 覆盖，
    否则 WFContentItemFilter 会被静默忽略。
  · Toggle 必须带 alarm + operation("turn") + state(0/1)。
  · 从闹钟实体读 label 要两层 aggrandizement：先转成 AlarmEntity，再取 label 属性。
"""
import plistlib
import sys
import datetime
import uuid
from pathlib import Path

import shortcutkit as sk
from shortcutkit import actions as A

ALARMS_URL = "https://felix-ma.github.io/self-calendar/alarms.json"
SHORTCUT_NAME = "排班闹钟"

LABEL = "排班"            # 闹钟标签识别关键字
ALARM_PREFIX = "排班·"     # 新建闹钟的标签前缀
DAY_SECONDS = 24 * 3600   # 闹钟只有「时:分」，超过一天会响错日子

# 用旧客户端版本号：shortcutkit 默认写 4018.0.4（取自 Shortcuts 10.0），
# 比 iOS 26 自带的版本高，导入时可能被要求「需要更新的系统」。之前能正常导入的
# 就是这一串，别跟着 shortcutkit 一起升。
CLIENT_VERSION = "1092.9.13.2"

ALARM_ENTITY_CLASS = "WFLinkEntityContentItem_com.apple.clock_AlarmEntity"

# Find Alarms 的 AppIntentDescriptor 必须手写，不能用 shortcutkit 目录里那份。
# 目录（取自 Shortcuts 10.0）给的是 AppIntentIdentifier=MTGetAlarmsIntent +
# BundleIdentifier=com.apple.clock：这个组合能取到闹钟，但 WFContentItemFilter
# 会被静默忽略 —— 2026-11-12 真机断点里「带排班过滤 8 个 / 不带过滤 8 个」
# 两个数一样，就是它。真机上真正生效的写法（vergenzt/shortcuts 里的三份实现
# 全部如此）是 AlarmEntity + com.apple.mobiletimer。
ALARM_ENTITY_DESCRIPTOR = {
    "ActionRequiresAppInstallation": True,
    "AppIntentIdentifier": "AlarmEntity",
    "BundleIdentifier": "com.apple.mobiletimer",
    "Name": "Clock",
    "TeamIdentifier": "0000000000",
}

# Toggle / Add 用同一套 bundle，只是 AppIntentIdentifier 不同（照参考实现）。
CLOCK_BUNDLE = "com.apple.mobiletimer"


def clock_descriptor(identifier):
    return {"ActionRequiresAppInstallation": True,
            "AppIntentIdentifier": identifier,
            "BundleIdentifier": CLOCK_BUNDLE,
            "Name": "Clock",
            "TeamIdentifier": "0000000000"}


def _override_descriptor(action, identifier):
    """把 shortcutkit 按目录自动写的 descriptor 换成真机验证过的那份。"""
    action["WFWorkflowActionParameters"]["AppIntentDescriptor"] = clock_descriptor(
        identifier)
    return action


def toggle(s, alarm, on):
    """开/关一个闹钟。不删除、不弹确认框。"""
    action = s.action(A.CLOCK_TOGGLE_ALARM, alarm=alarm, operation="turn",
                      state=on, ShowWhenRun=False)
    return _override_descriptor(action, "ToggleAlarmIntent")


def add_alarm(s, date_components, name):
    """建一个不重复的闹钟。

    不传 repeats 就是「不重复」：枚举里只有 sunday..saturday，没有「永不」。
    也没有 label / repeatSchedule 这两个参数 —— 多传会报「请为参数选取值」。
    """
    action = s.action(A.CLOCK_ADD_ALARM, dateComponents=date_components,
                      name=name, allowsSnooze=True)
    return _override_descriptor(action, "CreateAlarmIntent")

# 新建闹钟时，日期必须先落进这个变量再用它的 .Time（见第 4 段的说明）。
# 这是全文件唯一允许使用变量的地方，而且只在同一个代码块内写读。
WAKE_VAR = "排班时间"

LOGGER = "日志"            # debug 版的累积变量名

# 版本戳：出现在快捷指令最上面的注释里，用来判断手机上装的是不是最新生成的那份。
# 每次生成自动取当前时间，所以「重新下载后版本没变」就说明装的还是旧的。
BUILD_STAMP = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def act(s, identifier, name=None, **params):
    """加一个动作；name 非空时写 CustomOutputName，作为它的输出名。

    shortcutkit 默认用目录里的英文输出名（"Dates"、"Time Between Dates"），
    中文系统上编辑器显示的是「日期」这类本地化名字。为了让「引用名」和
    「实际输出名」始终一致（不一致时 iOS 会显示「未知变量」），这里统一
    自己起中文名，引用时也用同一个字符串。
    """
    if name:
        params["CustomOutputName"] = name
    return s.action(identifier, **params)


def ref(action):
    """引用某个动作的输出，输出名跟着 CustomOutputName 走。"""
    params = action.get("WFWorkflowActionParameters", {})
    return sk.ref(action, params.get("CustomOutputName"))


def num_if(s, subject, condition, number):
    """开一个「数字比较」的 If 块，返回 gid。

    为什么不用 shortcutkit 的 if_()：它把数值比较的值写成 Python 整数
    （`WFNumberValue` 存成 plist 的 <integer>），而 iOS 真实存的都是**字符串**
    （真机样本里是 `"2"`，vergenzt 的参考实现也写 `'0'`）。写成整数时 iOS 26
    的条件配不上，永远判为假 —— 2026-11-11 真机踩过：明明明天有班次，
    「还有多少秒 > 0」和「<= 86400」全失败，于是一条都挑不到。
    """
    gid = str(uuid.uuid4()).upper()
    # 先按整数喂进去过 shortcutkit 的校验，再把落盘的参数改成字符串。
    action = s.action("is.workflow.actions.conditional",
                      GroupingIdentifier=gid, WFControlFlowMode=0,
                      WFInput=sk.subject(subject),
                      WFCondition=sk.CONDITION[condition],
                      WFNumberValue=number)
    action["WFWorkflowActionParameters"]["WFNumberValue"] = str(number)
    return gid


def alarm_label(attachment):
    """把「循环项这个闹钟」读成它的 label。

    必须先声明成 AlarmEntity 再取属性，少第一层条件里读到的是空值。shortcutkit 的
    coerce() 只收内置内容类型（List 里没有这个实体类），所以这里手工拼那层
    aggrandizement —— 只是往 sk.property_() 的结果前面插一项，其余照旧。
    """
    value = attachment["Value"]
    return {"WFSerializationType": "WFTextTokenAttachment",
            "Value": {**value,
                      "Aggrandizements": [
                          {"Type": "WFCoercionVariableAggrandizement",
                           "CoercionItemClass": ALARM_ENTITY_CLASS},
                          # PropertyUserInfo 不能省：少了它 iOS 不认识这是「实体属性」，
                          # 直接把属性名字面量 "label" 当成值输出 —— 2026-11-12 真机
                          # 断点里就显示成英文单词 label，导致「含排班」条件恒不成立。
                          {"Type": "WFPropertyVariableAggrandizement",
                           "PropertyName": "label",
                           "PropertyUserInfo": {
                               "WFLinkEntityContentPropertyUserInfoPropertyIdentifier":
                                   "label"}}]}}


def find_alarms(s, name):
    """加一个 Find Alarms：取时钟里的全部闹钟。

    **不要传 WFContentItemFilter。** 2026-11-12 真机反复验证：iOS 26 上
    Find Alarms 完全无视过滤条件（带过滤 8 个、不带过滤还是 8 个；把
    descriptor 换成 AlarmEntity + com.apple.mobiletimer 也一样），只按
    WFContentItemInputParameter 返回全部闹钟。所以这里只给数据源，
    筛选一律放到循环里用 label 判断。

    descriptor 仍然覆盖成真机验证过的那份（见 ALARM_ENTITY_DESCRIPTOR）。
    """
    params = {"WFContentItemInputParameter": "Library"}
    action = act(s, A.CLOCK_FIND_ALARMS, name, **params)
    action["WFWorkflowActionParameters"]["AppIntentDescriptor"] = dict(
        ALARM_ENTITY_DESCRIPTOR)
    return action


def build_main(debug=False):
    """生成主指令。debug=True 时插入日志，用于真机排查。"""
    s = sk.Shortcut(SHORTCUT_NAME, color=4282601983, glyph=59649)

    # ── 断点弹窗 ──────────────────────────────────────────────────────────
    # 只在 debug=True 时插入。每个关键步骤弹一次「Show Alert」，把那一刻的
    # 真实值显示出来：点「好」继续往下一步，点「取消」就停在这里。
    # 这样不用把日志攒在变量里（2026-11-12 踩过：日志变量最后一片空白，
    # 变量在 If/Repeat 块里的写法也不确定能不能读出来），
    # 而且能一眼看出是哪一步的值不对。
    step = {"n": 0}

    def merge(*parts):
        """把若干片段拼成一个 WFTextTokenString。

        不能直接用 sk.text(*parts)：它只认 WFTextTokenAttachment，
        遇到已经是 WFTextTokenString 的片段会走 str()，把字典原文印出来
        （2026-11-12 踩过两次，测试 test_no_stringified_dicts_anywhere 会拦）。
        这里遇到 token string 就把它内部的 string 和 attachmentsByRange
        一起并进来，按 UTF-16 偏移重新编号。
        """
        string = ""
        attachments = {}
        for part in parts:
            if isinstance(part, dict) and part.get("WFSerializationType") == "WFTextTokenString":
                value = part["Value"]
                offset = len(string.encode("utf-16-le")) // 2
                for rng, ref in (value.get("attachmentsByRange") or {}).items():
                    start_at = int(rng.strip("{}").split(",")[0])
                    attachments[f"{{{offset + start_at}, 1}}"] = ref
                string += value.get("string", "")
            elif isinstance(part, dict) and part.get("WFSerializationType") == "WFTextTokenAttachment":
                attachments[f"{{{len(string.encode('utf-16-le')) // 2}, 1}}"] = part["Value"]
                string += "\ufffc"
            else:
                string += str(part)
        return {"WFSerializationType": "WFTextTokenString",
                "Value": {"string": string, "attachmentsByRange": attachments}}

    def stop(title, *parts):
        """插一个断点：弹出 title 和此时的真实值。

        点「好」继续，点「取消」终止，所以哪一步的值不对就停在哪一步。
        """
        if not debug:
            return
        step["n"] += 1
        s.action(A.ALERT,
                 WFAlertActionTitle=f"{step['n']}. {title}",
                 WFAlertActionMessage=merge(*parts) if parts else "（空）",
                 WFAlertActionCancelButtonShown=True)

    s.action("is.workflow.actions.comment", WFCommentActionText=(
        f"版本：{BUILD_STAMP}\n"
        "（这一行是生成时间戳，用来看手机上装的是不是最新的那份。"
        "导入后第一次打开快捷指令就能在最上面看到。）\n\n"
        "每天自动跑：拉取线上排班清单（alarms.json），用「当前时间」在 alarms[] 里"
        "挑出下一条还没到、且距现在不超过 24 小时的班次，在时钟里维护一个"
        "「排班·班次 时:分」的闹钟。只处理最近这一条，处理完就停，"
        "所以时钟里始终只有一个在用的排班闹钟。\n\n"
        "为什么在手机上算而不是让服务器算好：\n"
        "  alarms.json 是发布时的快照，可能一大早就生成了，也可能好几天不更新。"
        "服务器如果把「今天该建哪个」算死（以前的 due 字段），"
        "到了晚上再看就过期了 —— 2026-11-11 真机踩过：今天有白班却不建闹钟。\n"
        "  所以清单只描述「有哪些班次」，选哪一条完全由运行时的当前时间决定。\n\n"
        "为什么只取 24 小时内：\n"
        "  iOS 闹钟只有「时:分」没有日期，设 07:45 就是下一个 07:45 响。"
        "目标超过 24 小时还建，会把后天的闹钟提前到明天响。\n\n"
        "同名闹钟已存在就直接打开它，不重复新建，所以时钟里不会越攒越多；"
        "确实换班次/换时间时才关旧的、建一个新的。\n\n"
        "只用 iOS 26 就有的三个动作：查找闹钟 / 开关闹钟 / 添加闹钟。\n"
        "不用「更新闹钟」：iOS 27 才有，iOS 26 上会被静默丢弃，导致什么都不发生。\n"
        "不用「删除闹钟」：删除要弹确认框，关闭不用。\n\n"
        "建议在「自动化」里加每天 00:05 的时间触发，并关掉「运行前询问」。"))

    # ── 1. 拉线上清单并解析成字典 ───────────────────────────────────────────
    # detect.dictionary 不能省：服务器返回的 content-type 不一定被识别成 JSON，
    # 少了这步「获取字典值」拿不到东西。
    url = act(s, A.DOWNLOADURL, "清单", WFURL=ALARMS_URL, WFHTTPMethod="GET")
    data = act(s, A.DETECT_DICTIONARY, "清单字典", WFInput=ref(url))
    all_shift = act(s, A.GETVALUEFORKEY, "全部班次", WFInput=ref(data),
                    WFGetDictionaryValueType="Value", WFDictionaryKey="alarms")

    if debug:
        text_all = act(s, A.DETECT_TEXT, "清单文本", WFInput=ref(data))
        count_all = act(s, A.COUNT, "班次条数", Input=ref(all_shift),
                        WFCountType="Items")
        stop("拉取 alarms.json：解析出的 JSON 原文",
             sk.text(ref(text_all)))
        stop("alarms[] 条数（应为大于 0 的数）",
             sk.text(ref(count_all)))

    # ── 2. 遍历清单，只处理窗口内最早的那一条班次 ───────────────────────────
    # 这里**不用任何 Set Variable**（2026-11-12 真机踩过：变量在 Repeat/If
    # 块里写、块外读会得到空值，「已完成」标志读到空 → 循环体整段被跳过 →
    # 一条班次都挑不到，而且完全不报错）。所以改成不看结果、直接按条办事：
    # 每条落在窗口内的班次各自算标签、各自找闹钟、各自开关或新建，
    # 全程只用「动作输出」当引用（动作输出跨块可读，可靠）。
    #
    # 窗口是「at 还没到、且距现在不超过 24 小时」：
    # 闹钟只有「时:分」没有日期，超过一天建出来会在今天先响一次。
    # 每条命中后**都立刻 A.EXIT**（新建那段也一样），所以真正的效果是
    # 「只维护窗口内最早的那一个闹钟」，不会一天建出好几个。
    loop = s.repeat_each(ref(all_shift))

    at = act(s, A.GETVALUEFORKEY, "开始时刻", WFInput=sk.repeat_item(),
             WFGetDictionaryValueType="Value", WFDictionaryKey="at")
    when = act(s, A.DETECT_DATE, "日期", WFInput=ref(at))
    gap = act(s, A.GETTIMEBETWEENDATES, "还有多少秒",
              WFInput=sk.text(ref(when)),
              WFTimeUntilFromDate=sk.text(sk.current_date()),
              WFTimeUntilUnit="Total Time")   # Total Time 的单位就是秒

    stop("逐条比较：at 与「距今多少秒」",
         sk.text("at = ", ref(at), "\n距今 ", ref(gap), " 秒"))

    future = num_if(s, ref(gap), "greater_than", 0)             # 已经过了的不算
    soon = num_if(s, ref(gap), "less_or_equal", DAY_SECONDS)    # 只用 24 小时内的

    named = act(s, A.GETVALUEFORKEY, "闹钟名", WFInput=sk.repeat_item(),
                WFGetDictionaryValueType="Value", WFDictionaryKey="label")
    hhmm = act(s, A.GETVALUEFORKEY, "时刻", WFInput=sk.repeat_item(),
               WFGetDictionaryValueType="Value", WFDictionaryKey="time")
    # 目标标签 = 「排班·早班 07:45」，既是闹钟名，也是「同一个闹钟」的判据
    target = act(s, A.GETTEXT, "目标标签", WFTextActionText=sk.text(ref(named), " ", ref(hhmm)))
    if debug:
        # 直接把 .Time 属性塞进弹窗会印出自己的 aggrandizement 结构（用户反馈
        # 「显示的很奇怪」），所以先格式化成人能读的「07:45」再显示。
        pretty = act(s, A.FORMAT_DATE, "响铃时间",
                     WFDate=sk.text(ref(at)), WFDateFormatStyle="Custom",
                     WFDateFormat="HH:mm", WFTimeFormatStyle="None")
        stop("窗口内的班次：闹钟名与响铃时间",
             sk.text(ref(target), "\n响铃 ", ref(pretty), "（at=", ref(at), "）"))

    # ── 3. 这个班次的闹钟：同名就打开并收工，其余的「排班」关掉 ───────────
    # 这里**不用过滤**：2026-11-12 真机验证，iOS 26 上 Find Alarms 完全无视
    # WFContentItemFilter（换 AlarmEntity descriptor 也一样，带过滤 8 个、
    # 不带过滤还是 8 个），只会按 WFContentItemInputParameter 返回全部闹钟。
    # 所以取全部、在循环里用 label 自己判断。
    alarms = find_alarms(s, "全部闹钟")

    if debug:
        labels = act(s, A.DETECT_TEXT, "全部闹钟文本", WFInput=ref(alarms))
        stop("时钟里的全部闹钟（label 连成一串）",
             "共 ", sk.text(ref(alarms)), " 项：\n", sk.text(ref(labels)))

    each_alarm = s.repeat_each(ref(alarms))
    mine = s.if_(alarm_label(sk.repeat_item()), "contains", LABEL)
    same = s.if_(alarm_label(sk.repeat_item()), "is", sk.text(ref(target)))

    stop("同名闹钟：将打开它并结束", sk.text(ref(target)))
    # 同名闹钟：确保它是开着的（可能是之前关掉的）
    toggle(s, sk.repeat_item(), True)
    # 找到了就收工，别在循环外再建一个新的 —— 用「停止这个快捷指令」代替
    # 「记住找到过」的变量（变量跨块读不到，见文件顶部说明）。
    s.action(A.EXIT)

    s.otherwise(same)
    stop("旧闹钟：将关掉它", sk.text(alarm_label(sk.repeat_item())))
    # 不是它：关掉这个旧的排班闹钟（Toggle：不删除、不弹窗）
    toggle(s, sk.repeat_item(), False)

    s.end_if(same)
    s.end_if(mine)
    s.end_repeat_each(each_alarm)

    # ── 4. 时钟里没有同名闹钟（循环没提前结束）才新建 ──────────────────────
    # 日期必须先落进变量、再用变量的 .Time：这里**必须**用一次 Set Variable。
    # 2026-11-12 真机踩过：直接把「日期」动作的输出塞进 dateComponents，
    # iOS 26 认不出来，会卡在一个要求手动选日期的弹窗里，不填就中止
    # （原话「会一直卡死在一个弹窗，需要用户手动输入日期才可以创建」）。
    # 这个变量是在同一个代码块里写、同一个代码块里紧接着读，不跨块，
    # 所以不会碰上「块内写的变量块外读到空」那个坑。
    # 不传 repeats 就是「不重复」（枚举里没有「永不」，传了反而报参数错）。
    s.action(A.SETVARIABLE, WFInput=ref(when), WFVariableName=WAKE_VAR)
    alarm_time = sk.variable(WAKE_VAR)
    alarm_time["Value"]["Aggrandizements"] = [
        {"PropertyName": "Time", "Type": "WFPropertyVariableAggrandizement"}]
    add_alarm(s, sk.text(alarm_time), sk.text(ref(target)))
    s.action(A.NOTIFICATION,
             WFNotificationActionTitle="排班闹钟",
             WFNotificationActionBody=merge("已建闹钟：", sk.text(ref(target))))
    stop("已新建闹钟", sk.text(ref(target)))
    # 建完立刻收工：一天里「今天还有一班 + 明天有一班」时两条都落在 24 小时
    # 窗口内，不停手就会连建两个闹钟（后一个还会把前一个关掉），
    # 每天跑一遍就攒一堆关着的闹钟 —— 2026-11-12 用户反馈「会建很多闹钟」。
    s.action(A.EXIT)

    s.end_if(soon)
    s.end_if(future)
    s.end_repeat_each(loop)

    if debug:
        stop("跑完了（点「好」结束）")

    return s


def to_xml(shortcut):
    """shortcutkit 的 to_plist() 少了工作流名字，且客户端版本号偏高，这里补齐/改回。"""
    data = shortcut.to_plist()
    data["WFWorkflowName"] = SHORTCUT_NAME
    data["WFWorkflowClientVersion"] = CLIENT_VERSION
    return data


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug = "--debug" in sys.argv
    dst = Path(argv[0] if argv else "排班闹钟.plist")
    data = to_xml(build_main(debug=debug))
    dst.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
    print(f"已生成 {dst}（{len(data['WFWorkflowActions'])} 个动作，版本 {BUILD_STAMP}）")
