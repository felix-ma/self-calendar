#!/usr/bin/env python3
"""生成「排班闹钟」快捷指令（.plist → 交给 playground 签名）。

流程：找今天三个排班日历里的事件 → 先删掉旧闹钟（label 含「排班」）
     → 按每个事件的开始时间提前 N 分钟建新闹钟。
"""
import plistlib, uuid, json, sys
from pathlib import Path

def U() -> str:
    return str(uuid.uuid4()).upper()

def var(name, aggs=None):
    v = {"Type": "Variable", "VariableName": name}
    if aggs:
        v["Aggrandizements"] = aggs
    return {"Value": v, "WFSerializationType": "WFTextTokenAttachment"}

def token(parts):
    """parts: 字符串 或 ActionOutput 引用 dict"""
    text, atts, pos = "", {}, 0
    for p in parts:
        if isinstance(p, str):
            text += p; pos += len(p)
        else:
            text += "\ufffc"
            atts["{%d, 1}" % pos] = p
            pos += 1
    return {"Value": {"string": text, "attachmentsByRange": atts},
            "WFSerializationType": "WFTextTokenString"}

def out(uid, name):
    return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}

def act(ident, params=None, uid=None):
    d = {"WFWorkflowActionIdentifier": ident}
    if params is not None:
        d["WFWorkflowActionParameters"] = params
    return d

LEAD = "60"          # 兜底提前分钟：事件备注里没写「闹钟提前：N」时用这个
LABEL = "排班"
PATTERN = "闹钟提前：(\\d+)"

A_FIND_CAL, A_FIND_ALM, A_START, A_TITLE = U(), U(), U(), U()
A_NOTES, A_MATCH, A_GROUP, A_NUM, A_ADJUST = U(), U(), U(), U(), U()
A_DEFAULT = U()
G_LOOP, G_IF = U(), U()

def calendar_filter():
    """开始时间在未来 1 天 且 标题含「班」——AND 关系，避免把别的日程也建成闹钟。"""
    return {
        "Value": {
            "WFActionParameterFilterPrefix": 1,          # 1 = 全部条件同时满足
            "WFActionParameterFilterTemplates": [
                {"Operator": 1002, "Bounded": True, "Removable": False,
                 "Property": "Start Date", "Values": {"Unit": 16, "Number": 1}},
                {"Operator": 99, "Removable": True, "Property": "Title",
                 "Values": {"String": "班", "Unit": 4}},
                {"Operator": 4, "Removable": True, "Property": "Is All Day",
                 "Values": {"Bool": False}},
            ],
            "WFContentPredicateBoundedDate": False,
        },
        "WFSerializationType": "WFContentPredicateTableTemplate",
    }


actions = [
    act("is.workflow.actions.comment", {"WFCommentActionText":
        "每天自动跑：找出今天标题含「班」的日程（早班·门店 这类），"
        "先删掉时钟里所有名字带「排班」的旧闹钟，再按日程里的通勤提前量建当天的闹钟。\n\n"
        "提前量来自日历事件备注里的「闹钟提前：N 分钟」那一行——"
        "跟日历提醒用的通勤时间完全一致，不用手改。\n"
        "备注里没有这行时，退回默认 " + LEAD + " 分钟。\n"
        "建议配一个「时间」自动化，每天早上 00:05 运行，并关掉「运行前询问」。"
    }),

    # 1. 找今天的排班事件
    act("is.workflow.actions.filter.calendarevents",
        {"WFContentItemInputParameter": "Library",
         "WFContentItemLimitEnabled": False,
         "UUID": A_FIND_CAL,
         "WFContentItemFilter": calendar_filter()}),

    # 2. 找时钟里已有的排班闹钟
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTGetAlarmsIntent",
        {"AppIntentDescriptor": {"ActionRequiresAppInstallation": True,
                                 "AppIntentIdentifier": "AlarmEntity",
                                 "BundleIdentifier": "com.apple.mobiletimer",
                                 "Name": "Clock", "TeamIdentifier": "0000000000"},
         "ShowWhenRun": False,
         "UUID": A_FIND_ALM,
         "WFContentItemFilter": {
             "Value": {"WFActionParameterFilterPrefix": 1,
                       "WFActionParameterFilterTemplates": [
                           {"Operator": 99, "Property": "label", "Removable": True,
                            "Values": {"String": LABEL, "Unit": 4}}],
                       "WFContentPredicateBoundedDate": False},
             "WFSerializationType": "WFContentPredicateTableTemplate"}}),

    # 3. 删掉旧闹钟
    act("com.apple.mobiletimer.DeleteAlarmIntent",
        {"AppIntentDescriptor": {"AppIntentIdentifier": "DeleteAlarmIntent",
                                 "BundleIdentifier": "com.apple.mobiletimer",
                                 "Name": "Clock", "TeamIdentifier": "0000000000"},
         "entities": {"Value": out(A_FIND_ALM, "闹钟"),
                      "WFSerializationType": "WFTextTokenAttachment"}}),

    # 4. 兜底提前量：备注里没写就用这个
    act("is.workflow.actions.number",
        {"WFNumberActionNumber": int(LEAD), "UUID": A_DEFAULT}),
    act("is.workflow.actions.setvariable",
        {"WFInput": {"Value": out(A_DEFAULT, "数字"),
                     "WFSerializationType": "WFTextTokenAttachment"},
         "WFVariableName": "提前分钟"}),

    # 5. 循环每个事件
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_LOOP, "WFControlFlowMode": 0,
         "WFInput": {"Value": out(A_FIND_CAL, "日历日程"),
                     "WFSerializationType": "WFTextTokenAttachment"}}),

    # 6. 取开始时间
    act("is.workflow.actions.properties.calendarevents",
        {"WFContentItemPropertyName": "Start Date",
         "WFInput": var("Repeat Item"), "UUID": A_START}),

    # 7. 取标题（= 班次·门店）
    act("is.workflow.actions.properties.calendarevents",
        {"WFContentItemPropertyName": "Title",
         "WFInput": var("Repeat Item"), "UUID": A_TITLE}),

    # 8. 取备注，里面写着脚本算好的通勤提前量
    act("is.workflow.actions.properties.calendarevents",
        {"WFContentItemPropertyName": "Notes",
         "WFInput": var("Repeat Item"), "UUID": A_NOTES}),

    # 9. 从备注里抠出「闹钟提前：N」
    act("is.workflow.actions.text.match",
        {"text": token([out(A_NOTES, "备注")]),
         "WFMatchTextPattern": PATTERN,
         "WFMatchTextCaseSensitive": False,
         "UUID": A_MATCH}),

    # 10. 命中了才覆盖提前量
    act("is.workflow.actions.conditional",
        {"GroupingIdentifier": G_IF,
         "WFControlFlowMode": 0,
         "WFInput": {"Type": "Variable",
                     "Variable": {"Value": out(A_MATCH, "匹配文本"),
                                  "WFSerializationType": "WFTextTokenAttachment"}},
         "WFCondition": 101}),          # 101 = 有任何值

    # 10a. 取第 1 个捕获组
    act("is.workflow.actions.text.match.getgroup",
        {"matches": {"Value": out(A_MATCH, "匹配文本"),
                     "WFSerializationType": "WFTextTokenAttachment"},
         "WFGetGroupType": "Group At Index",
         "WFGroupIndex": 1,
         "UUID": A_GROUP}),

    # 10b. 转成数字
    act("is.workflow.actions.detect.number",
        {"WFInput": {"Value": out(A_GROUP, "分组"),
                     "WFSerializationType": "WFTextTokenAttachment"},
         "UUID": A_NUM}),

    # 10c. 存成变量「提前分钟」
    act("is.workflow.actions.setvariable",
        {"WFInput": {"Value": out(A_NUM, "数字"),
                     "WFSerializationType": "WFTextTokenAttachment"},
         "WFVariableName": "提前分钟"}),

    # 10d. 条件结束
    act("is.workflow.actions.conditional",
        {"GroupingIdentifier": G_IF, "WFControlFlowMode": 2}),

    # 11. 按提前分钟往回推出发时间
    act("is.workflow.actions.adjustdate",
        {"WFAdjustOperation": "Subtract",
         "WFDate": token([out(A_START, "开始时间")]),
         "WFDuration": {"Value": {"Magnitude": token([var("提前分钟")]),
                                  "Unit": "min"},
                        "WFSerializationType": "WFQuantityFieldValue"},
         "UUID": A_ADJUST}),

    # 12. 存成命名变量，供下面按「时间」取值
    act("is.workflow.actions.setvariable",
        {"WFInput": {"Value": out(A_ADJUST, "调整后的日期"),
                     "WFSerializationType": "WFTextTokenAttachment"},
         "WFVariableName": "排班时间"}),

    # 13. 建闹钟
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTCreateAlarmIntent",
        {"AppIntentDescriptor": {"AppIntentIdentifier": "CreateAlarmIntent",
                                 "BundleIdentifier": "com.apple.clock",
                                 "Name": "Clock", "TeamIdentifier": "0000000000"},
         "ShowWhenRun": False,
         "dateComponents": token([var("排班时间", [
             {"PropertyName": "Time", "Type": "WFPropertyVariableAggrandizement"}])]),
         "label": token([out(A_TITLE, "标题")]),
         "name": token([out(A_TITLE, "标题")]),
         "repeatSchedule": {"displayString": "Never", "value": 0},
         "allowsSnooze": True}),

    # 14. 循环结束
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_LOOP, "WFControlFlowMode": 2}),
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
