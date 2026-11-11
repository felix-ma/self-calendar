#!/usr/bin/env python3
"""生成「排班闹钟」快捷指令（XML plist → 交给 playground 签名）。

用法：
    python3 shortcut/build_shortcut.py /tmp/main.plist

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

参数写法的硬要求（照着真实样本抄，别自己发挥）：
  · MTToggleAlarmIntent / MTCreateAlarmIntent / MTGetAlarmsIntent 都是 AppIntent，
    只认 AppIntentDescriptor + 该动作真实存在的参数；多传参数会报
    「请为此操作中的每个参数选取值，然后终止了」。
  · Toggle 必须带 alarm + operation("turn") + state(0/1)；少了 operation 会报参数没选值。
  · 从闹钟实体里读 label，必须先转成 AlarmEntity 再取属性，两层 aggrandizement：
    先 WFCoercionVariableAggrandizement(WFLinkEntityContentItem_com.apple.clock_AlarmEntity)，
    再 WFPropertyVariableAggrandizement(label)。少第一层，条件里读到的是空值。
"""
import plistlib
import sys
import uuid
from pathlib import Path

ALARMS_URL = "https://felix-ma.github.io/self-calendar/alarms.json"
SHORTCUT_NAME = "排班闹钟"

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


def text(s):
    """纯文本参数（setvariable 这类不吃裸字符串）。"""
    return attach(token([s]))


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

# 闹钟实体 → 可读取的属性。两层：先转成 AlarmEntity，再取 label。
ALARM_ENTITY_COERCION = {
    "CoercionItemClass": "WFLinkEntityContentItem_com.apple.clock_AlarmEntity",
    "Type": "WFCoercionVariableAggrandizement",
}
ALARM_LABEL_PROP = {
    "PropertyName": "label",
    "PropertyUserInfo": {
        "WFLinkEntityContentPropertyUserInfoPropertyIdentifier": "label"},
    "Type": "WFPropertyVariableAggrandizement",
}
# 读「循环项这个闹钟的 label」用的完整访问链
ALARM_LABEL_AGGS = [ALARM_ENTITY_COERCION, ALARM_LABEL_PROP]

TIME_AGG = {"PropertyName": "Time", "Type": "WFPropertyVariableAggrandizement"}


def build_main():
    A_DL, A_DICT, A_ALL = U(), U(), U()
    A_AT, A_DATE, A_GAP = U(), U(), U()
    A_AT2, A_DATE2, A_LABEL, A_HHMM, A_ALM = U(), U(), U(), U(), U()
    G_LOOP, G_TODO, G_POS, G_24 = U(), U(), U(), U()
    G_MAIN, G_ALM, G_IF, G_SAME, G_NEW = U(), U(), U(), U(), U()

    return [
        act("is.workflow.actions.comment", {"WFCommentActionText":
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

        # 2. 取全部班次列表
        act("is.workflow.actions.getvalueforkey",
            {"WFInput": attach(out(A_DICT, "清单字典")),
             "WFGetDictionaryValueType": "Value",
             "WFDictionaryKey": "alarms"},
            name="全部班次", uid=A_ALL),

        # 3. 选中标志：字符串比较最稳，别用「变量是否有值」那种模糊判断
        act("is.workflow.actions.setvariable",
            {"WFInput": text("否"), "WFVariableName": "已完成"}),

        # 4. 逐条比较，挑第一条符合条件的
        act("is.workflow.actions.repeat.each",
            {"GroupingIdentifier": G_LOOP, "WFControlFlowMode": 0,
             "WFInput": attach(out(A_ALL, "全部班次"))}),

        # 4a. 已经选中了就跳过剩下的
        if_block(G_TODO, 0,
                 WFInput=cond_input(ref("已完成")),
                 WFCondition=4,                             # is
                 WFConditionalActionString="否"),

        # 4b. 这一条的响铃时刻
        act("is.workflow.actions.getvalueforkey",
            {"WFInput": attach(ref("Repeat Item")),
             "WFGetDictionaryValueType": "Value",
             "WFDictionaryKey": "at"},
            name="开始时刻", uid=A_AT),

        act("is.workflow.actions.detect.date",
            {"WFInput": attach(out(A_AT, "开始时刻"))},
            name="日期", uid=A_DATE),

        # 4c. 距现在还有多少秒（Total Time 单位就是秒）
        act("is.workflow.actions.gettimebetweendates",
            {"WFInput": token([out(A_DATE, "日期")]),
             "WFTimeUntilFromDate": token([{"Type": "CurrentDate"}]),
             "WFTimeUntilUnit": "Total Time"},
            name="还有多少秒", uid=A_GAP),

        # 4d. 已经过了的不算
        if_block(G_POS, 0,
                 WFInput=cond_input(out(A_GAP, "还有多少秒")),
                 WFCondition=2,                             # is greater than
                 WFNumberValue="0"),

        # 4e. 只用 24 小时内的（闹钟没日期，超过一天会响错日子）
        if_block(G_24, 0,
                 WFInput=cond_input(out(A_GAP, "还有多少秒")),
                 WFCondition=1,                             # is less than or equal to
                 WFNumberValue="86400"),

        act("is.workflow.actions.setvariable",
            {"WFInput": attach(ref("Repeat Item")), "WFVariableName": "目标"}),

        act("is.workflow.actions.setvariable",
            {"WFInput": text("是"), "WFVariableName": "已完成"}),

        if_block(G_24, 2),
        if_block(G_POS, 2),
        if_block(G_TODO, 2),
        act("is.workflow.actions.repeat.each",
            {"GroupingIdentifier": G_LOOP, "WFControlFlowMode": 2}),

        # 5. 挑到了才继续
        if_block(G_MAIN, 0,
                 WFInput=cond_input(ref("已完成")),
                 WFCondition=4,
                 WFConditionalActionString="是"),

        # 5a. 目标 → 日期
        act("is.workflow.actions.getvalueforkey",
            {"WFInput": attach(ref("目标")),
             "WFGetDictionaryValueType": "Value",
             "WFDictionaryKey": "at"},
            name="响铃时刻", uid=A_AT2),

        act("is.workflow.actions.detect.date",
            {"WFInput": attach(out(A_AT2, "响铃时刻"))},
            name="响铃日期", uid=A_DATE2),

        # 5b. 目标 → 班次名（形如「排班·早班」）
        act("is.workflow.actions.getvalueforkey",
            {"WFInput": attach(ref("目标")),
             "WFGetDictionaryValueType": "Value",
             "WFDictionaryKey": "label"},
            name="闹钟名", uid=A_LABEL),

        # 5c. 目标 → 「07:45」，用来拼标签，也是"同一个闹钟"的判据
        act("is.workflow.actions.getvalueforkey",
            {"WFInput": attach(ref("目标")),
             "WFGetDictionaryValueType": "Value",
             "WFDictionaryKey": "time"},
            name="时刻", uid=A_HHMM),

        # 5d. 目标标签 = 「排班·早班 07:45」
        act("is.workflow.actions.setvariable",
            {"WFInput": attach(token([out(A_LABEL, "闹钟名"), " ", out(A_HHMM, "时刻")])),
             "WFVariableName": "目标标签"}),

        # 6. 取时钟里的排班闹钟
        act("com.apple.mobiletimer-framework.MobileTimerIntents.MTGetAlarmsIntent",
            {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="AlarmEntity"),
             "ShowWhenRun": False,
             # 两个都必填，缺一个真机就报「请为此操作中的每个参数选取值，然后终止了」：
             #  · WFContentItemInputParameter = 从哪儿找（时钟里的闹钟）
             #  · WFContentItemFilter         = 筛选条件（名字含「排班」）
             # filter 里的 Property 必须小写 'label'；Operator 99 = 包含。
             "WFContentItemInputParameter": "Library",
             "WFContentItemFilter": {
                 "Value": {
                     "WFActionParameterFilterPrefix": 1,
                     "WFActionParameterFilterTemplates": [{
                         "Operator": 99,
                         "Property": "label",
                         "Removable": True,
                         "Values": {"String": LABEL, "Unit": 4},
                     }],
                     "WFContentPredicateBoundedDate": False,
                 },
                 "WFSerializationType": "WFContentPredicateTableTemplate",
             }},
            name="闹钟", uid=A_ALM),

        # 7. 记下「已经找到目标闹钟」，循环结束后据此决定要不要新建
        act("is.workflow.actions.setvariable",
            {"WFInput": text("否"), "WFVariableName": "已存在"}),

        # 8. 逐个看
        act("is.workflow.actions.repeat.each",
            {"GroupingIdentifier": G_ALM, "WFControlFlowMode": 0,
             "WFInput": attach(out(A_ALM, "闹钟"))}),

        # 9. 只看名字含「排班」的，别人的闹钟一律不碰
        if_block(G_IF, 0,
                 WFInput=cond_input(ref("Repeat Item", ALARM_LABEL_AGGS)),
                 WFCondition=99,                             # contains
                 WFConditionalActionString=LABEL),

        # 10. 名字正好是目标标签 → 就是它
        if_block(G_SAME, 0,
                 WFInput=cond_input(ref("Repeat Item", ALARM_LABEL_AGGS)),
                 WFCondition=4,                             # is
                 WFConditionalActionString=token([ref("目标标签")])),

        act("is.workflow.actions.setvariable",
            {"WFInput": text("是"), "WFVariableName": "已存在"}),

        # 11. 同名闹钟：确保它是开着的（可能是之前关掉的）
        act("com.apple.mobiletimer-framework.MobileTimerIntents.MTToggleAlarmIntent",
            {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="ToggleAlarmIntent"),
             # 只需要一层 WFTextTokenAttachment，多包一层 iOS 就认不出实体，会弹窗
             "alarm": attach(ref("Repeat Item")),
             "operation": "turn",
             "state": 1,
             "ShowWhenRun": False}),

        if_block(G_SAME, 1),

        # 12. 不是它：关掉这个旧的排班闹钟（Toggle：不删除、不弹窗）
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

        # 13. 没有同名闹钟才新建
        if_block(G_NEW, 0,
                 WFInput=cond_input(ref("已存在")),
                 WFCondition=4,
                 WFConditionalActionString="否"),

        act("com.apple.mobiletimer-framework.MobileTimerIntents.MTCreateAlarmIntent",
            {"AppIntentDescriptor": dict(CLOCK, AppIntentIdentifier="CreateAlarmIntent"),
             "ShowWhenRun": False,
             # 取日期的 .Time 属性：闹钟只要「时:分」
             "dateComponents": token([out(A_DATE2, "响铃日期", [TIME_AGG])]),
             "label": token([ref("目标标签")]),
             "name": token([ref("目标标签")]),
             # 不重复。这套写法在用户真机上建出过闹钟，别改。
             "repeatSchedule": {"displayString": "Never", "value": 0},
             "allowsSnooze": True}),

        if_block(G_NEW, 2),

        # 14. 没挑到：提示一句，不建闹钟
        if_block(G_MAIN, 1),
        act("is.workflow.actions.notification",
            {"WFNotificationActionTitle": "排班闹钟",
             "WFNotificationActionBody":
                 "24 小时内没有要建的排班闹钟，本次不建。"}),
        if_block(G_MAIN, 2),
    ]


def wrap(actions, name):
    return {
        "WFWorkflowActions": actions,
        "WFWorkflowName": name,
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
    data = wrap(build_main(), SHORTCUT_NAME)
    dst.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
    print(f"已生成 {dst}（{len(data['WFWorkflowActions'])} 个动作）")
