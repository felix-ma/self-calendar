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
  2. 用「当前时间」逐条比较，挑出第一条满足下面两条的：
     · at 还没到（已过的班次跳过）
     · 距现在不超过 24 小时（闹钟没有日期，超过一天会响错日子）
     —— 这个选择必须在运行时做。alarms.json 是发布时的快照，
        服务器先算好「今天该建哪个」会过期：晚上再跑就漏了当天的班次。
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

shortcutkit 目录查出来、之前手写 plist 写错的几处（别再改回去）：
  · MTCreateAlarmIntent 的真实参数只有 dateComponents / name / repeats /
    allowsSnooze / OpenWhenRun，**没有 label、没有 repeatSchedule**。
    以前多传的这两个无效键，就是真机报「请为此操作中的每个参数选取值」的元凶。
  · repeats 的可选值只有 sunday..saturday，没有「永不」→ 不重复就**整个不传**。
  · 三个闹钟动作的 AppIntentDescriptor 都用 com.apple.clock（不是 com.apple.mobiletimer），
    AppIntentIdentifier 分别是 CreateAlarmIntent / MTGetAlarmsIntent / ToggleAlarmIntent。
  · Toggle 必须带 alarm + operation("turn") + state(0/1)。
  · 从闹钟实体读 label 要两层 aggrandizement：先转成 AlarmEntity，再取 label 属性。
"""
import plistlib
import sys
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

LOGGER = "日志"            # debug 版的累积变量名


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
                          {"Type": "WFPropertyVariableAggrandizement",
                           "PropertyName": "label"}]}}


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
        "每天自动跑：拉取线上排班清单（alarms.json），用「当前时间」在 alarms[] 里"
        "挑出下一条还没到、且距现在不超过 24 小时的班次，在时钟里维护一个"
        "「排班·班次 时:分」的闹钟。\n\n"
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

    # ── 2. 逐条比较，挑第一条符合条件的 ─────────────────────────────────────
    # 用字符串「是/否」做标志位，「is」比较最稳。
    s.action(A.SETVARIABLE, WFInput=sk.text("否"), WFVariableName="已完成")

    loop = s.repeat_each(ref(all_shift))
    todo = s.if_(sk.coerce(sk.variable("已完成"), "WFStringContentItem"),
                 "is", "否")

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

    s.action(A.SETVARIABLE, WFInput=sk.repeat_item(), WFVariableName="目标")
    s.action(A.SETVARIABLE, WFInput=sk.text("是"), WFVariableName="已完成")

    s.end_if(soon)
    s.end_if(future)
    s.end_if(todo)
    s.end_repeat_each(loop)

    stop("循环结束：挑到了吗？（已完成=是 才有下一步）",
         sk.text(sk.variable("已完成")))

    # ── 3. 挑到了才继续 ────────────────────────────────────────────────────
    picked = s.if_(sk.coerce(sk.variable("已完成"), "WFStringContentItem"),
                   "is", "是")

    at2 = act(s, A.GETVALUEFORKEY, "响铃时刻", WFInput=sk.variable("目标"),
              WFGetDictionaryValueType="Value", WFDictionaryKey="at")
    when2 = act(s, A.DETECT_DATE, "响铃日期", WFInput=ref(at2))
    named = act(s, A.GETVALUEFORKEY, "闹钟名", WFInput=sk.variable("目标"),
                WFGetDictionaryValueType="Value", WFDictionaryKey="label")
    hhmm = act(s, A.GETVALUEFORKEY, "时刻", WFInput=sk.variable("目标"),
               WFGetDictionaryValueType="Value", WFDictionaryKey="time")

    # 目标标签 = 「排班·早班 07:45」，既是闹钟名，也是「同一个闹钟」的判据
    s.action(A.SETVARIABLE, WFInput=sk.text(ref(named), " ", ref(hhmm)),
             WFVariableName="目标标签")
    stop("挑中的班次：闹钟名与响铃时间",
         sk.text(ref(named), " ", ref(hhmm),
                 "\n响铃 ", sk.property_(ref(when2), "Time")))

    # ── 4. 处理时钟里已有的排班闹钟 ────────────────────────────────────────
    # 过滤器交给 Find Alarms 自己做（label contains「排班」），循环里再判一次，
    # 这样即使某版 iOS 忽略过滤条件，也绝不会碰到别人的闹钟。
    alarms = act(s, A.CLOCK_FIND_ALARMS, "闹钟",
                 WFContentItemInputParameter="Library",
                 WFContentItemFilter=sk.where(("label", "contains", LABEL)))

    if debug:
        count_alarm = act(s, A.COUNT, "闹钟条数", Input=ref(alarms),
                          WFCountType="Items")
        # 再查一次「不带任何过滤」，两条数一对比就知道是过滤失效、
        # 还是压根读不到闹钟。
        all_alarms = act(s, A.CLOCK_FIND_ALARMS, "全部闹钟",
                         WFContentItemInputParameter="Library")
        count_all_alarm = act(s, A.COUNT, "全部闹钟条数", Input=ref(all_alarms),
                              WFCountType="Items")
        stop("时钟里的闹钟数量",
             sk.text("带「排班」过滤：", ref(count_alarm),
                     " 个\n不带过滤：", ref(count_all_alarm), " 个\n\n",
                     "两个数不同 → 过滤有效\n都为 0 → 读不到闹钟\n",
                     "都为 N、过滤不起作用 → 过滤被忽略"))

    s.action(A.SETVARIABLE, WFInput=sk.text("否"), WFVariableName="已存在")

    each_alarm = s.repeat_each(ref(alarms))
    stop("时钟里的闹钟 label", sk.text(alarm_label(sk.repeat_item())))
    mine = s.if_(alarm_label(sk.repeat_item()), "contains", LABEL)
    same = s.if_(alarm_label(sk.repeat_item()), "is", sk.text(sk.variable("目标标签")))

    s.action(A.SETVARIABLE, WFInput=sk.text("是"), WFVariableName="已存在")
    stop("同名闹钟：将打开它", sk.text(sk.variable("目标标签")))
    # 同名闹钟：确保它是开着的（可能是之前关掉的）
    s.action(A.CLOCK_TOGGLE_ALARM, alarm=sk.repeat_item(),
             operation="turn", state=True, ShowWhenRun=False)

    s.otherwise(same)
    stop("旧闹钟：将关掉它", sk.text(alarm_label(sk.repeat_item())))
    # 不是它：关掉这个旧的排班闹钟（Toggle：不删除、不弹窗）
    s.action(A.CLOCK_TOGGLE_ALARM, alarm=sk.repeat_item(),
             operation="turn", state=False, ShowWhenRun=False)

    s.end_if(same)
    s.end_if(mine)
    s.end_repeat_each(each_alarm)

    # ── 5. 没有同名闹钟才新建 ──────────────────────────────────────────────
    missing = s.if_(sk.coerce(sk.variable("已存在"), "WFStringContentItem"),
                    "is", "否")
    # 取日期的 .Time 属性：闹钟只要「时:分」。不传 repeats 就是「不重复」
    # （枚举里没有「永不」，传了反而报参数错）。
    s.action(A.CLOCK_ADD_ALARM,
             dateComponents=sk.text(sk.property_(ref(when2), "Time")),
             name=sk.text(sk.variable("目标标签")),
             allowsSnooze=True)
    stop("已新建闹钟", sk.text(sk.variable("目标标签")))
    s.end_if(missing)
    stop("已有同名闹钟，未新建（已存在=是）",
         sk.text(sk.variable("已存在")))

    # ── 6. 没挑到：提示一句，不建闹钟 ──────────────────────────────────────
    s.otherwise(picked)
    stop("没挑到班次，本次不建闹钟", "已完成=", sk.text(sk.variable("已完成")))
    s.action(A.NOTIFICATION,
             WFNotificationActionTitle="排班闹钟",
             WFNotificationActionBody="24 小时内没有要建的排班闹钟，本次不建。")
    s.end_if(picked)

    # ── 7. 诊断版：把日志复制到剪贴板并显示出来 ──────────────────────────
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
    print(f"已生成 {dst}（{len(data['WFWorkflowActions'])} 个动作）")
