#!/usr/bin/env python3
"""生成「排班闹钟」快捷指令（.plist → 交给 playground 签名）。

流程（不读日历、不读备注，直接请求线上 alarms.json）：
  1. 拉 https://felix-ma.github.io/self-calendar/alarms.json
  2. 取 due 字段：paiban.py 已经算好「下一次该建的闹钟」
     （今天的班次过了/今天没排班就自动落到明天；全都过了就是空）
  3. due 为空 → 什么都不动，只提示一句
  4. due 有值 → 找时钟里名字含「排班」的闹钟：
       - 第一个 → **原地更新**时间和名字（不删除、不新建）
       - 其余的 → 关掉（清理历史遗留，不删除、不弹窗）
       - 一个都没有 → 才新建

为什么用「更新」而不是「每天新建」：闹钟只有「时:分」没有日期，每天新建一个
再关掉旧的，用久了时钟里会堆一堆已关闭的废闹钟。原地更新只会存在一个。

为什么不用「删除」：删除需要用户确认弹窗，更新和关闭都不需要。
"""
import plistlib, sys, uuid
from pathlib import Path

ALARMS_URL = "https://felix-ma.github.io/self-calendar/alarms.json"

LABEL = "排班"            # 闹钟标签识别关键字
ALARM_PREFIX = "排班·"     # 新建闹钟的标签前缀，保证下次运行能认出自己建的

NO, YES = "否", "是"       # 变量「已更新」的取值


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


def var(name, aggs=None):
    return attach(ref(name, aggs))


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

# Update Alarm 属于 com.apple.clock（assistant 那一套）
CLOCK_UPDATE = dict(CLOCK, BundleIdentifier="com.apple.clock")

# 从闹钟实体里取 label 的属性访问器
ALARM_LABEL_AGG = {"PropertyName": "label",
                   "PropertyUserInfo": {
                       "WFLinkEntityContentPropertyUserInfoPropertyIdentifier": "label"},
                   "Type": "WFPropertyVariableAggrandizement"}

TIME_AGG = {"PropertyName": "Time", "Type": "WFPropertyVariableAggrandizement"}

A_DL, A_DICT, A_DUE, A_AT, A_DATE, A_LABEL, A_ALM = U(), U(), U(), U(), U(), U(), U()
G_MAIN, G_ALM, G_LAB, G_UPD, G_MAKE = U(), U(), U(), U(), U()

actions = [
    act("is.workflow.actions.comment", {"WFCommentActionText":
        "每天自动跑：拉取线上排班闹钟清单（alarms.json），取 paiban.py 算好的 due，"
        "把时钟里名字含「排班」的闹钟原地更新成 due 的时间，不新建、不删除。\n\n"
        "due 已经包含「今天班次过了就看明天、明天没排班就不建」的判断，"
        "所以这里不做日期判断，只看 due 有没有值。\n"
        "用「更新」而不是「删了重建」：时钟里只会有一个排班闹钟，不会越积越多；"
        "更新也不需要你点确认（删除才需要）。\n"
        "建议在「自动化」里加每天 00:05 的时间触发，并关掉「运行前询问」。"
    }),

    # 0. 标记「还没更新过」
    act("is.workflow.actions.setvariable",
        {"WFInput": token([NO]), "WFVariableName": "已更新"}),

    # 1. 拉线上清单
    act("is.workflow.actions.downloadurl",
        {"WFURL": ALARMS_URL, "WFHTTPMethod": "GET"},
        name="清单", uid=A_DL),

    # 2. 明确解析成字典：服务器返回的 content-type 不一定被识别成 JSON，
    #    少了这步「获取字典值」会拿不到东西
    act("is.workflow.actions.detect.dictionary",
        {"WFInput": attach(out(A_DL, "清单"))},
        name="清单字典", uid=A_DICT),

    # 3. 取 due（下一次该生效的闹钟；没有就是空）
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DICT, "清单字典")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "due"},
        name="下次闹钟", uid=A_DUE),

    # 4. due 有值才继续
    if_block(G_MAIN, 0,
             WFInput=cond_input(out(A_DUE, "下次闹钟")),
             WFCondition=101),

    # 5. due.at → 日期
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DUE, "下次闹钟")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "at"},
        name="开始时刻", uid=A_AT),

    act("is.workflow.actions.detect.date",
        {"WFInput": attach(out(A_AT, "开始时刻"))},
        name="日期", uid=A_DATE),

    # 6. due.label → 闹钟名（形如「排班·早班」）
    act("is.workflow.actions.getvalueforkey",
        {"WFInput": attach(out(A_DUE, "下次闹钟")),
         "WFGetDictionaryValueType": "Value",
         "WFDictionaryKey": "label"},
        name="闹钟名", uid=A_LABEL),

    # 7. 取时钟里所有闹钟（不靠过滤条件：真机上 label 过滤不可靠，
    #    改在循环里逐个判断，既不会漏也不会误伤）
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTGetAlarmsIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="AlarmEntity"),
         "ShowWhenRun": False,
         "WFContentItemInputParameter": "Library"},
        name="闹钟", uid=A_ALM),

    # 8. 逐个看：名字含「排班」的才处理
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_ALM, "WFControlFlowMode": 0,
         "WFInput": attach(out(A_ALM, "闹钟"))}),

    if_block(G_LAB, 0,
             WFInput=cond_input(ref("Repeat Item", [ALARM_LABEL_AGG])),
             WFCondition=99,
             WFConditionalActionString=LABEL),

    # 9. 第一个：原地更新（时间 + 名字），不新建
    if_block(G_UPD, 0,
             WFInput=cond_input(ref("已更新")),
             WFCondition=4,
             WFConditionalActionString=NO),

    act("com.apple.clock.UpdateAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK_UPDATE,
                                     AppIntentIdentifier="UpdateAlarmIntent"),
         "ShowWhenRun": False,
         # 只需要一层 WFTextTokenAttachment，多包一层 iOS 就认不出实体，会弹窗
         "alarm": attach(ref("Repeat Item")),
         # 取日期的 .Time 属性：闹钟只要「时:分」
         "time": token([out(A_DATE, "日期", [TIME_AGG])]),
         "label": token([out(A_LABEL, "闹钟名")]),
         "isEnabled": True,
         "allowsSnooze": True}),

    act("is.workflow.actions.setvariable",
        {"WFInput": token([YES]), "WFVariableName": "已更新"}),

    # 10. 其余同名的（历史遗留）：关掉，清库存
    if_block(G_UPD, 1),
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTToggleAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="ToggleAlarmIntent"),
         "alarm": attach(ref("Repeat Item")),
         "operation": "turn",
         "state": 0,
         "ShowWhenRun": False}),
    if_block(G_UPD, 2),

    if_block(G_LAB, 2),
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_ALM, "WFControlFlowMode": 2}),

    # 11. 一个排班闹钟都没有 → 才建一个
    if_block(G_MAKE, 0,
             WFInput=cond_input(ref("已更新")),
             WFCondition=4,
             WFConditionalActionString=NO),

    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTCreateAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="CreateAlarmIntent"),
         "ShowWhenRun": False,
         "dateComponents": token([out(A_DATE, "日期", [TIME_AGG])]),
         "label": token([out(A_LABEL, "闹钟名")]),
         "name": token([out(A_LABEL, "闹钟名")]),
         "repeatSchedule": {"displayString": "Never", "value": 0},
         "allowsSnooze": True}),

    if_block(G_MAKE, 1),
    act("is.workflow.actions.notification",
        {"WFNotificationActionTitle": "排班闹钟",
         "WFNotificationActionBody": "已把排班闹钟更新为下一次的时间。"}),
    if_block(G_MAKE, 2),

    # 12. 没有 due：什么都不动
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
