#!/usr/bin/env python3
"""生成「排班闹钟」快捷指令（.plist → 交给 playground 签名）。

流程：
  1. 找今天三个排班日历里标题含「班」的日程
  2. 找时钟里所有闹钟，只删名字含「排班」的（二次确认，避免误删别的闹钟）
  3. 按每个日程的开始时间，减去备注里的「闹钟提前：N 分钟」，建新闹钟
"""
import plistlib, uuid, sys
from pathlib import Path


def U() -> str:
    return str(uuid.uuid4()).upper()


def ref(name, aggs=None):
    """裸引用：attachmentsByRange 里只能放这个。"""
    r = {"Type": "Variable", "VariableName": name}
    if aggs:
        r["Aggrandizements"] = aggs
    return r


def out(uid, name):
    """裸 ActionOutput 引用。"""
    return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}


def attach(r):
    """需要包一层 WFTextTokenAttachment 的场合（各动作的输入槽）。"""
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


def act(ident, params=None):
    d = {"WFWorkflowActionIdentifier": ident}
    if params is not None:
        d["WFWorkflowActionParameters"] = params
    return d


def cond_input(r):
    """条件动作的 WFInput 要额外套一层 Variable（跟其它动作的输入槽规则不同）。"""
    return {"Type": "Variable", "Variable": attach(r)}


def if_block(group, mode, **kw):
    p = {"GroupingIdentifier": group, "WFControlFlowMode": mode}
    p.update(kw)
    return act("is.workflow.actions.conditional", p)


LEAD = 60            # 兜底提前分钟：备注里没有「闹钟提前：N」时用这个
LABEL = "排班"        # 闹钟标签的识别关键字（Find Alarms 与 If 都用它）
ALARM_PREFIX = "排班·"  # 新建闹钟的标签前缀，保证下次能认出来
PATTERN = "闹钟提前：(\\d+)"

CLOCK = {"ActionRequiresAppInstallation": True,
         "BundleIdentifier": "com.apple.mobiletimer",
         "Name": "Clock", "TeamIdentifier": "0000000000"}

# 从闹钟实体里取 label 的属性访问器
ALARM_LABEL_AGG = {"PropertyName": "label",
                   "PropertyUserInfo": {
                       "WFLinkEntityContentPropertyUserInfoPropertyIdentifier": "label"},
                   "Type": "WFPropertyVariableAggrandizement"}

A_CAL, A_ALM, A_ALMTEXT = U(), U(), U()
A_START, A_TITLE, A_NOTES = U(), U(), U()
A_MATCH, A_GROUP, A_NUM, A_ADJUST, A_DEFAULT = U(), U(), U(), U(), U()
G_DEL, G_IFDEL, G_LOOP, G_IF = U(), U(), U(), U()


def calendar_filter():
    """未来 1 天内、标题含「班」、非全天——AND 关系。"""
    return {
        "Value": {
            "WFActionParameterFilterPrefix": 1,
            "WFActionParameterFilterTemplates": [
                # 1002 = 「是今天」，官方格式不带 Values；
                # 之前写的 {"Unit":16,"Number":1} 是无效参数，会导致条件变红/失效
                {"Operator": 1002, "Bounded": True, "Removable": False,
                 "Property": "Start Date", "Values": {}},
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
        "每天自动跑：找出今天标题含「班」的日程，删掉时钟里名字含「排班」的旧闹钟，"
        "再按日程备注里的「闹钟提前：N 分钟」建新闹钟。\n\n"
        "提前量跟日历提醒用的通勤时间一致，来自备注第一行；没有这行时退回 "
        + str(LEAD) + " 分钟。\n"
        "建议在「自动化」里加一个每天 00:05 的「时间」触发，并关掉「运行前询问」。"
    }),

    # 1. 找今天的排班日程
    act("is.workflow.actions.filter.calendarevents",
        {"WFContentItemInputParameter": "Library",
         "WFContentItemLimitEnabled": False,
         "UUID": A_CAL,
         "WFContentItemFilter": calendar_filter()}),

    # 2. 找时钟里名字带「排班」的闹钟
    #    属性名是小写 "label"（与参考实现一致，枚举里的 identifier 就是 label）；
    #    后面第 3b 步还会再判一次，双保险：即使这里没过滤住也不会误删别的闹钟
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTGetAlarmsIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="AlarmEntity"),
         "ShowWhenRun": False,
         "UUID": A_ALM,
         "WFContentItemInputParameter": "Library",
         "WFContentItemFilter": {
             "Value": {
                 "WFActionParameterFilterPrefix": 1,
                 "WFActionParameterFilterTemplates": [{
                     "Operator": 99, "Property": "label", "Removable": True,
                     "Values": {"String": LABEL, "Unit": 4},
                 }],
                 "WFContentPredicateBoundedDate": False,
             },
             "WFSerializationType": "WFContentPredicateTableTemplate",
         }}),

    # 3. 逐个检查，只处理名字含「排班」的
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_DEL, "WFControlFlowMode": 0,
         "WFInput": attach(out(A_ALM, "闹钟"))}),

    # 3a. 名字含「排班」才删，别的闹钟一律不碰
    #     直接取闹钟实体的 label 属性（比转成文本再匹配准，见参考实现
    #     WFLinkEntityContentPropertyUserInfoPropertyIdentifier: 'label'）
    if_block(G_IFDEL, 0,
             WFInput=cond_input(ref("Repeat Item", [ALARM_LABEL_AGG])),
             WFCondition=99,
             WFConditionalActionString=LABEL),

    act("com.apple.mobiletimer.DeleteAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="DeleteAlarmIntent"),
         # entities 只需要一层 WFTextTokenAttachment，多包一层 iOS 就认不出实体，
         # 会退化成弹窗让用户手选闹钟。
         "entities": attach(ref("Repeat Item"))}),

    if_block(G_IFDEL, 2),

    # 3c. 闹钟循环结束
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_DEL, "WFControlFlowMode": 2}),

    # 4. 兜底提前量
    act("is.workflow.actions.number",
        {"WFNumberActionNumber": LEAD, "UUID": A_DEFAULT}),
    act("is.workflow.actions.setvariable",
        {"WFInput": attach(out(A_DEFAULT, "数字")), "WFVariableName": "提前分钟"}),

    # 5. 循环每个日程
    act("is.workflow.actions.repeat.each",
        {"GroupingIdentifier": G_LOOP, "WFControlFlowMode": 0,
         "WFInput": attach(out(A_CAL, "日历日程"))}),

    act("is.workflow.actions.properties.calendarevents",
        {"WFContentItemPropertyName": "Start Date",
         "WFInput": var("Repeat Item"), "UUID": A_START}),

    act("is.workflow.actions.properties.calendarevents",
        {"WFContentItemPropertyName": "Title",
         "WFInput": var("Repeat Item"), "UUID": A_TITLE}),

    act("is.workflow.actions.properties.calendarevents",
        {"WFContentItemPropertyName": "Notes",
         "WFInput": var("Repeat Item"), "UUID": A_NOTES}),

    # 6. 从备注里抠出「闹钟提前：N」
    act("is.workflow.actions.text.match",
        {"text": token([out(A_NOTES, "备注")]),
         "WFMatchTextPattern": PATTERN,
         "WFMatchTextCaseSensitive": False,
         "UUID": A_MATCH}),

    # 6a. 命中才覆盖提前量
    if_block(G_IF, 0, WFInput=cond_input(out(A_MATCH, "匹配文本")), WFCondition=101),

    act("is.workflow.actions.text.match.getgroup",
        {"matches": attach(out(A_MATCH, "匹配文本")),
         "WFGetGroupType": "Group At Index",
         "WFGroupIndex": 1,
         "UUID": A_GROUP}),

    act("is.workflow.actions.detect.number",
        {"WFInput": attach(out(A_GROUP, "分组")), "UUID": A_NUM}),

    act("is.workflow.actions.setvariable",
        {"WFInput": attach(out(A_NUM, "数字")), "WFVariableName": "提前分钟"}),

    if_block(G_IF, 2),

    # 7. 开始时间往回推
    act("is.workflow.actions.adjustdate",
        {"WFAdjustOperation": "Subtract",
         "WFDate": token([out(A_START, "开始时间")]),
         "WFDuration": {"Value": {"Magnitude": ref("提前分钟"),
                                  "Unit": "min"},
                        "WFSerializationType": "WFQuantityFieldValue"},
         "UUID": A_ADJUST}),

    act("is.workflow.actions.setvariable",
        {"WFInput": attach(out(A_ADJUST, "调整后的日期")),
         "WFVariableName": "排班时间"}),

    # 8. 建闹钟：时间取「排班时间」的 .Time
    act("com.apple.mobiletimer-framework.MobileTimerIntents.MTCreateAlarmIntent",
        {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="CreateAlarmIntent"),
         "ShowWhenRun": False,
         "dateComponents": token([ref("排班时间", [
             {"PropertyName": "Time", "Type": "WFPropertyVariableAggrandizement"}])]),
         # 标签必须带「排班」前缀：下次运行靠它认出这是自己建的闹钟，
         # 否则删旧时匹配不到，闹钟会一天天堆积
         "label": token([ALARM_PREFIX, out(A_TITLE, "标题")]),
         "name": token([ALARM_PREFIX, out(A_TITLE, "标题")]),
         "repeatSchedule": {"displayString": "Never", "value": 0},
         "allowsSnooze": True}),

    # 9. 日程循环结束
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
