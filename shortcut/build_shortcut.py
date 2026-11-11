#!/usr/bin/env python3
"""生成「排班闹钟」快捷指令（.plist → 交给 playground 签名）。

流程（不读日历、不读备注，直接请求线上 alarms.json）：
  1. 拉 https://felix-ma.github.io/self-calendar/alarms.json
  2. 取 due 字段：paiban.py 已经算好「下一次该生效的闹钟」
     （今天的班次过了 / 今天没排班就自动落到明天；全都过了就是空）
  3. due 为空 → 什么都不建，只提示一句
  4. due 有值 → 目标标签 = 「due.label + 空格 + due 的 HH:mm」，例如「排班·早班 07:45」
     · 时钟里已有同名闹钟 → 打开它（顺便把其它「排班」闹钟关掉），不新建
     · 没有同名闹钟     → 把所有「排班」旧闹钟关掉，再按 due.at 新建一个

为什么标签里带时间：
  只有「查找 / 开关 / 添加」三个动作可用（见下），改不了闹钟时间，
  只能关旧建新。把时间编进标签后，同一班次同一时刻的闹钟只会存在一个，
  重复的班次不会每天再新增，时钟里不会越攒越多。

为什么不用 Update Alarm（原地改时间）：
  它是 iOS 27 才有的动作。iOS 26 导入时该动作会被静默丢弃，
  结果「什么都没发生、也不报错」—— 2026-11-11 真机踩过。

为什么不用 Delete：
  删除必须弹确认框要用户点；关闭（Toggle）不需要，静默生效。
"""
import plistlib, sys, uuid
from pathlib import Path

ALARMS_URL = "https://felix-ma.github.io/self-calendar/alarms.json"

LABEL = "排班"            # 闹钟标签识别关键字
ALARM_PREFIX = "排班·"     # 新建闹钟的标签前缀


def U() -> str:
    return str(uuid.uuid4()).upper()


def ref(name, aggs=None):
    """裸引用：attachmentsByRange 里只能放这个。"""
    r = {"Type": "Variable", "VariableName": name}
    if aggs:
        r["Aggrandizements"] = aggs
    return r


def out(uid, name, aggs=None):
    """裸 ActionOutput 引用。"""
    r = {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}
    if aggs:
        r["Aggrandizements"] = aggs
    return r


def attach(r):
    """各动作输入槽要包一层 WFTextTokenAttachment。"""
    return {"Value": r, "WFSerializationType": "WFTextTokenAttachment"}


def token(parts):
    """文本插值。parts：字符串 或裸引用 dict（ref()/out() 的返回值）。"""
    text, atts, pos = "", {}, 0
    for p in parts:
        if isinstance(p, str):
            text += p
            pos += len(p)
        else:
            text += "\ufffc"
            atts["{%d, 1}" % pos] = p
            pos += 1
    return {"Value": {"string": text, "attachmentsByRange": atts},
            "WFSerializationType": "WFTextTokenString"}


def act(ident, params=None, name=None, uid=None):
    """name 非空时写 CustomOutputName —— 引用它的 OutputName 必须用同一个字符串，
    否则 iOS 会显示「未知变量」。"""
    p = dict(params or {})
    if uid:
        p["UUID"] = uid
    if name:
        p["CustomOutputName"] = name
    d = {"WFWorkflowActionIdentifier": ident}
    if p:
        d["WFWorkflowActionParameters"] = p
    return d


def cond_input(r):
    """条件动作的 WFInput 要额外套一层 Variable（跟别的动作输入槽规则不同）。"""
    return {"Type": "Variable", "Variable": attach(r)}


def if_block(group, mode, **kw):
    p = {"GroupingIdentifier": group, "WFControlFlowMode": mode}
    p.update(kw)
    return act("is.workflow.actions.conditional", p)


CLOCK = {"ActionRequiresAppInstallation": True,
         "BundleIdentifier": "com.apple.mobiletimer",
         "Name": "Clock", "TeamIdentifier": "0000000000"}

# 从闹钟实体里取 label 的属性访问器
ALARM_LABEL_AGG = {"PropertyName": "label",
                   "PropertyUserInfo": {
                       "WFLinkEntityContentPropertyUserInfoPropertyIdentifier": "label"},
                   "Type": "WFPropertyVariableAggrandizement"}

TIME_AGG = {"PropertyName": "Time", "Type": "WFPropertyVariableAggrandizement"}

A_DL, A_DICT, A_DUE, A_AT, A_DATE, A_LABEL, A_HHMM, A_TARGET, A_ALM = (
    U(), U(), U(), U(), U(), U(), U(), U(), U())
G_MAIN, G_ALM, G_IF, G_SAME, G_NEW = U(), U(), U(), U(), U()

actions = [
    act("is.workflow.actions.comment", {"WFCommentActionText":
        "每天自动跑：拉取线上排班闹钟清单（alarms.json），取 paiban.py 算好的 due，"
        "在时钟里维护一个「排班·班次 时:分」的闹钟。\n\n"
        "due 已经包含「今天班次过了就看明天、明天没排班就不建」的判断，"
        "所以这里不做日期判断，只看 due 有没有值。\n\n"
        "同名闹钟已存在就直接打开它，不重复新建，所以时钟里不会越攒越多；"
        "确实换班次/换时间时才关旧的、建一个新的。\n\n"
        "只用 iOS 26 就有的三个动作：查找闹钟 / 开关闹钟 / 添加闹钟。\n"
        "不用「更新闹钟」：iOS 27 才有，iOS 26 上会被静默丢弃，导致什么都不发生。\n"
        "不用「删除闹钟」：删除要弹确认框，关闭不用。\n\n"
        "建议在「自动化」里加每天 00:05 的时间触发，并关掉「运行前询问」。"
    }),

    # 1. 拉线上清单
    act("is.workflow.actions.downloadurl",
        {"WFURL": ALARMS_URL, "WFHTTPMethod": "GET"},
        name="清单", uid=A_DL),

    # 1a. 明确解析成字典：服务器返回的 content-type 不一定被识别成 JSON，
    #     少了这步「获取字典值」会拿不到东西
    act("is.workflow.actions.detect.dictionary",
        {"WFInput": attach(out(A_DL, "清单"))},
        name="清单字典", uid=A_DICT),

    # 2. 取 due（下一次该生效的闹钟；没有就是空）
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DICT, "清单字典")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "due"},
        name="下次闹钟", uid=A_DUE),

    # 3. due 有值才继续
    if_block(G_MAIN, 0,
             WFInput=cond_input(out(A_DUE, "下次闹钟")),
             WFCondition=101),

    # 4. due.at → 日期
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DUE, "下次闹钟")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "at"},
        name="开始时刻", uid=A_AT),

    act("is.workflow.actions.detect.date",
        {"WFInput": attach(out(A_AT, "开始时刻"))},
        name="日期", uid=A_DATE),

    # 5. due.label → 班次名（形如「排班·早班」）
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DUE, "下次闹钟")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "label"},
        name="闹钟名", uid=A_LABEL),

    # 6. due.time → 「14:45」，用来拼标签，也是"同一个闹钟"的判据
    #    直接用清单里的 time 字段，不自己格式化日期，省一处格式歧义
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DUE, "下次闹钟")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "time"},
        name="时刻", uid=A_HHMM),

    # 7. 目标标签 = 「排班·早班 07:45」
    act("is.workflow.actions.setvariable",
        {"WFInput": attach(token([out(A_LABEL, "闹钟名"), " ", out(A_HHMM, "时刻")])),
         "WFVariableName": "目标标签"}),

    # 8. 取时钟里所有闹钟（不靠过滤条件：真机上 label 过滤不可靠，
    #    改在循环里逐个判断，既不会漏也不会误伤）
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTGetAlarmsIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="AlarmEntity"),
         "ShowWhenRun": False,
         "WFContentItemInputParameter": "Library"},
        name="闹钟", uid=A_ALM),

    # 9. 记下「已经找到目标闹钟」，循环结束后据此决定要不要新建
    # 字面量文本必须序列化成 WFTextTokenString，直接写裸字符串 iOS 认不出
    act("is.workflow.actions.setvariable",
        {"WFInput": attach(token(["否"])), "WFVariableName": "已存在"}),

    # 10. 逐个看
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_ALM, "WFControlFlowMode": 0,
         "WFInput": attach(out(A_ALM, "闹钟"))}),

    # 11. 只看名字含「排班」的，别人的闹钟一律不碰
    if_block(G_IF, 0,
             WFInput=cond_input(ref("Repeat Item", [ALARM_LABEL_AGG])),
             WFCondition=99,
             WFConditionalActionString=LABEL),

    # 12. 名字正好是目标标签 → 就是它
    if_block(G_SAME, 0,
             WFInput=cond_input(ref("Repeat Item", [ALARM_LABEL_AGG])),
             WFCondition=4,
             WFConditionalActionString=token([ref("目标标签")])),

    act("is.workflow.actions.setvariable",
        {"WFInput": attach(token(["是"])), "WFVariableName": "已存在"}),

    # 13. 同名闹钟：确保它是开着的（可能是之前关掉的）
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTToggleAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="ToggleAlarmIntent"),
         # 只需要一层 WFTextTokenAttachment，多包一层 iOS 就认不出实体，会弹窗
         "alarm": attach(ref("Repeat Item")),
         "operation": "turn",
         "state": 1,
         "ShowWhenRun": False}),

    if_block(G_SAME, 1),

    # 14. 不是它：关掉这个旧的排班闹钟（Toggle：不删除、不弹窗）
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTToggleAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="ToggleAlarmIntent"),
         "alarm": attach(ref("Repeat Item")),
         "operation": "turn",
         "state": 0,
         "ShowWhenRun": False}),

    if_block(G_SAME, 2),
    if_block(G_IF, 2),
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_ALM, "WFControlFlowMode": 2}),

    # 15. 没有同名闹钟才新建
    if_block(G_NEW, 0,
             WFInput=cond_input(ref("已存在")),
             WFCondition=4,
             WFConditionalActionString="否"),

    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTCreateAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="CreateAlarmIntent"),
         "ShowWhenRun": False,
         # 取日期的 .Time 属性：闹钟只要「时:分」
         "dateComponents": token([out(A_DATE, "日期", [TIME_AGG])]),
         "label": token([ref("目标标签")]),
         "name": token([ref("目标标签")]),
         "repeatSchedule": {"displayString": "Never", "value": 0},
         "allowsSnooze": True}),

    if_block(G_NEW, 2),

    # 16. 没有 due：提示一句，不建闹钟
    if_block(G_MAIN, 1),
    act("is.workflow.actions.notification",
        {"WFNotificationActionTitle": "排班闹钟",
         "WFNotificationActionBody":
             "今天和之后的排班都没有需要新建的闹钟，本次不建。"}),
    if_block(G_MAIN, 2),
]

data = {
    "WFWorkflowActions": actions,
    "WFWorkflowClientVersion": "1092.9.13.2",
    "WFWorkflowMinimumClientVersion": 900,
    "WFWorkflowMinimumClientVersionString": "900",
    "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": 59649,
                       "WFWorkflowIconStartColor": 4282601983},
    "WFWorkflowInputContentItemClasses": [],
    "WFWorkflowOutputContentItemClasses": [],
    "WFWorkflowHasOutputFallback": False,
    "WFWorkflowHasShortcutInputVariables": False,
    "WFQuickActionSurfaces": [],
}

if __name__ == "__main__":
    dst = Path(sys.argv[1] if len(sys.argv) > 1 else "排班闹钟.plist")
    dst.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
    print(f"已生成 {dst}（{len(actions)} 个动作）")
