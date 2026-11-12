#!/usr/bin/env python3
"""生成器回归测试：把踩过的真机坑写成断言。

跑法（必须用项目 venv，生成器依赖 shortcutkit）：
    .venv/bin/python -m unittest discover -s shortcut -v
    # 或
    .venv/bin/python shortcut/test_build_shortcut.py

能测什么：生成器是纯 Python，`build_main()` 返回的就是要落盘的 plist 字典，
所以结构错误（参数名、类型、引用名、块配对）都能在这里拦住。

测不了什么：iOS 的运行时语义。比如条件在真机上算成真还是假、某个动作在
某个 iOS 版本上会不会被静默丢弃 —— 这些只有真机才知道。下面每个用例的
docstring 都写了它对应哪一次线上事故。
"""
import plistlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_shortcut  # noqa: E402
from shortcutkit import actions as A  # noqa: E402


def params(action):
    return action["WFWorkflowActionParameters"]


def identifiers(actions):
    return [a["WFWorkflowActionIdentifier"] for a in actions]


# 个别用例里用更短的名字
ids_of = identifiers


class GeneratedShortcut(unittest.TestCase):
    """整份指令的公共检查。"""

    @classmethod
    def setUpClass(cls):
        cls.plist = build_shortcut.to_xml(build_shortcut.build_main())
        cls.actions = cls.plist["WFWorkflowActions"]

    # ── 结构完整性 ────────────────────────────────────────────────────────

    def test_plist_roundtrips(self):
        """能写成 XML 再读回来，且动作数不变（落盘格式坏会导致导入失败）。"""
        raw = plistlib.dumps(self.plist, fmt=plistlib.FMT_XML)
        again = plistlib.loads(raw)
        self.assertEqual(len(again["WFWorkflowActions"]), len(self.actions))

    def test_root_keys(self):
        """根键齐全；客户端版本号用 iOS 26 认得的那串。"""
        for key in ("WFWorkflowName", "WFWorkflowActions",
                    "WFWorkflowMinimumClientVersion",
                    "WFWorkflowIcon", "WFWorkflowInputContentItemClasses"):
            self.assertIn(key, self.plist)
        self.assertEqual(self.plist["WFWorkflowName"], build_shortcut.SHORTCUT_NAME)
        self.assertEqual(self.plist["WFWorkflowClientVersion"],
                         build_shortcut.CLIENT_VERSION)
        self.assertEqual(self.plist["WFWorkflowClientVersion"], "1092.9.13.2")

    def test_blocks_are_paired(self):
        """每个 If / Repeat 块都有配对的括号，否则快捷指令编辑器里整段错位。"""
        groups = {}
        for action in self.actions:
            p = params(action)
            if "GroupingIdentifier" in p:
                groups.setdefault(p["GroupingIdentifier"], []).append(
                    p.get("WFControlFlowMode"))
        for gid, modes in groups.items():
            self.assertEqual(modes[0], 0, f"{gid} 不是以块开始")
            self.assertEqual(modes[-1], 2, f"{gid} 没有闭合")
            if 1 in modes:
                self.assertEqual(modes.count(1), 1, f"{gid} 出现了多个 Otherwise")

    def test_no_dangling_or_misnamed_refs(self):
        """引用必须指向真实存在的输出，且名字与输出名一致。

        对应事故：编辑器里显示「未知变量」，真机运行到那一步就空值。
        """
        outputs = {}
        for action in self.actions:
            p = params(action)
            if "CustomOutputName" in p:
                outputs[p["UUID"]] = p["CustomOutputName"]

        problems = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("Type") == "ActionOutput":
                    uid, name = node.get("OutputUUID"), node.get("OutputName")
                    if uid not in outputs:
                        problems.append(f"悬空引用 {name} ({uid})")
                    elif outputs[uid] != name:
                        problems.append(f"引用名 {name} != 输出名 {outputs[uid]}")
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.actions)
        self.assertEqual(problems, [], "引用有问题：" + "; ".join(problems))

    # ── 曾经把真机搞挂的具体写法 ──────────────────────────────────────────

    def test_number_conditions_are_strings(self):
        """If 的数值比较必须存成字符串。

        对应事故（2026-11-11）：shortcutkit 的 if_() 把 WFNumberValue 写成整数，
        iOS 26 的条件配不上，「还有多少秒 > 0」「<= 86400」全判为假，
        结果一条班次都挑不到 —— 明明明天有白班，却提示「没有要建的闹钟」。
        """
        found = 0
        for action in self.actions:
            p = params(action)
            if "WFNumberValue" in p:
                found += 1
                self.assertIsInstance(
                    p["WFNumberValue"], str,
                    "WFNumberValue 必须是字符串，整数会让 iOS 的条件永远判假")
            if "WFAnotherNumber" in p:
                self.assertIsInstance(p["WFAnotherNumber"], str)
        self.assertGreaterEqual(found, 2, "24 小时窗口的两个数值条件不见了")

    def test_add_alarm_carries_the_three_extra_params(self):
        """Add Alarm 要带上目录里没有的 label / repeatSchedule / ShowWhenRun。

        对应两次真机事故：
        - 「请为此操作中的每个参数选取值，然后终止了」：当时传了 appintents
          参数表里没有的 label / repeatSchedule。但那条报错真正的原因是
          dateComponents 直接塞动作输出（见 test_alarm_time_comes_from_the_variable）。
        - 「名字没有传入具体名称」「创建时间还是需要用户手动选择」：
          少传 label / repeatSchedule / ShowWhenRun 时，iOS 26 建出来的闹钟
          没名字，还会停在编辑弹窗要人手动选时间。2026-11-11 确实建出过闹钟的
          老版本就是带着这三个参数的，所以补回来。
        """
        adds = [a for a in self.actions
                if a["WFWorkflowActionIdentifier"] == A.CLOCK_ADD_ALARM]
        self.assertEqual(len(adds), 1, "应该正好有一个「添加闹钟」")
        p = params(adds[0])
        self.assertIn("dateComponents", p)
        self.assertIn("name", p)
        # 时钟 App 认 label；和 name 指向同一个标签
        self.assertIn("label", p)
        self.assertEqual(p["label"], p["name"], "label 和 name 应该是同一个标签")
        self.assertEqual(p["repeatSchedule"], {"displayString": "Never", "value": 0},
                         "repeatSchedule 必须显式写 Never，否则 iOS 会问重复方式")
        self.assertIs(p.get("ShowWhenRun"), False)
        # 不传 repeats 才是不重复（枚举里只有 sunday..saturday）
        self.assertNotIn("repeats", p)

    def test_clock_descriptors(self):
        """三个闹钟动作的 descriptor 用真机验证过的那份，别跟着目录走。

        shortcutkit 目录给的是 com.apple.clock + MTGetAlarmsIntent / ToggleAlarmIntent /
        CreateAlarmIntent。这套能取到闹钟，但 Find Alarms 的 WFContentItemFilter
        会被静默忽略（真机断点：带过滤 8 个 / 不带过滤 8 个），所以这里显式覆盖成
        com.apple.mobiletimer，Find 的 AppIntentIdentifier 是 AlarmEntity。
        """
        expected = {
            A.CLOCK_ADD_ALARM: "CreateAlarmIntent",
            A.CLOCK_FIND_ALARMS: "AlarmEntity",
            A.CLOCK_TOGGLE_ALARM: "ToggleAlarmIntent",
        }
        seen = set()
        for action in self.actions:
            identifier = action["WFWorkflowActionIdentifier"]
            if identifier in expected:
                desc = params(action)["AppIntentDescriptor"]
                self.assertEqual(desc["BundleIdentifier"], "com.apple.mobiletimer")
                self.assertEqual(desc["Name"], "Clock")
                self.assertEqual(desc["AppIntentIdentifier"], expected[identifier])
                seen.add(identifier)
        self.assertEqual(seen, set(expected), "三个闹钟动作应全部出现")

    def test_toggle_has_all_required_params(self):
        """Toggle 必须带 alarm + operation + state，少一个就是「参数没选值」。"""
        toggles = [a for a in self.actions
                   if a["WFWorkflowActionIdentifier"] == A.CLOCK_TOGGLE_ALARM]
        self.assertGreaterEqual(len(toggles), 2, "应该有关掉旧的、打开同名两个分支")
        for action in toggles:
            p = params(action)
            for key in ("alarm", "operation", "state"):
                self.assertIn(key, p)
            self.assertEqual(p["operation"], "turn")
            self.assertIsInstance(p["state"], bool)
        self.assertEqual({params(t)["state"] for t in toggles}, {True, False})

    def test_find_alarms_has_input_and_no_filter(self):
        """只查一次全部闹钟，并且明确**不带**过滤条件。

        事故（2026-11-12）：iOS 26 上 Find Alarms 完全无视
        WFContentItemFilter —— 带过滤 8 个、不带过滤还是 8 个（换
        AlarmEntity descriptor 也一样）。既然过滤不起作用，就不要带上，
        免得以后有人以为它在生效、把判断逻辑写错。
        判断改在循环里用 label 做，见 test_loop_decides_by_label。
        """
        finds = [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == A.CLOCK_FIND_ALARMS]
        self.assertEqual(len(finds), 1)
        p = params(finds[0])
        self.assertEqual(p["WFContentItemInputParameter"], "Library")
        self.assertNotIn("WFContentItemFilter", p,
                         "iOS 26 不认这个过滤，带上只会误导")

    def test_loop_decides_by_label(self):
        """同名就打开并提前收工；不是它就关掉。"""
        self.assertEqual(
            len([a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == A.EXIT]), 2,
            "找到同名、以及新建成功后，都要用「停止这个快捷指令」收工，"
            "不能靠变量记状态，也不能接着处理后面的班次")
        # 判断「是不是排班闹钟」「是不是同名」全靠 text.match + count + 数字比较，
        # 见 test_no_variable_in_condition_values。这里只确认收工动作在。

    def test_no_variable_in_condition_values(self):
        """「如果」的比较值里不能放变量，否则 iOS 15.4 上参数显示成红色。

        对应事故（2026-11-12）：15.4 真机上「如果 是」「如果 包含」的比较值
        直接变红、缺参数 —— 那两处正是拿「目标标签」这个动作输出做的比较。
        15.4 的「如果」只接受固定值。所以判断改成
        「匹配文本 → 数条数 → 条数 > 0」这一套，纯数字比较两代系统都认。
        """
        for action in self.actions:
            if action["WFWorkflowActionIdentifier"] != "is.workflow.actions.conditional":
                continue
            p = params(action)
            # 15.4 连「输入是实体属性变量」都标红，所以「如果」里一律不做文本比较：
            # 比较值不能是变量，也不能用 contains/is 这类文本条件。
            self.assertNotIsInstance(p.get("WFConditionalActionString"), dict,
                                     "「如果」的比较值放了变量，15.4 会红")
            self.assertNotIn(p.get("WFCondition"), (4, 99),
                             "15.4 上「如果 是 / 包含」会标红，改成匹配文本+数条数")
        # 判断「有没有同名」现在靠 text.match + count + 数字比较
        self.assertGreaterEqual(
            len([a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == A.TEXT_MATCH]), 2,
            "命中数判断要用「匹配文本」，不能靠「如果 包含 <变量>」")

    def test_alarm_name_uses_plain_text_not_entity_property(self):
        """读闹钟名字只能用「整项转文本」，不能用实体 label 属性。

        2026-11-12 真机对照（iOS 26 与 15.4 一致）：
          重复项目直接转文本        → 有名字
          强制转成文本类型再转文本    → 有名字
          实体 → label 属性          → **读不出来**
          实体 → dateComponents      → 有时刻
        所以主指令整份都不该出现「转成 AlarmEntity 再取 label」这种链路，
        出现就说明又退化回去了。
        """
        def find_aggs(node, out):
            if isinstance(node, dict):
                aggs = node.get("Aggrandizements")
                if isinstance(aggs, list):
                    out.append(aggs)
                for value in node.values():
                    find_aggs(value, out)
            elif isinstance(node, list):
                for value in node:
                    find_aggs(value, out)

        found = []
        find_aggs(self.actions, found)
        for aggs in found:
            props = [a.get("PropertyName") for a in aggs
                     if a.get("Type") == "WFPropertyVariableAggrandizement"]
            self.assertNotIn("label", props,
                             "实体 label 属性读不出来，名字要用「转文本」")

class Diagnostics(unittest.TestCase):
    """诊断版（--debug）。日志本身也可能写成坏结构，这里一并测。"""

    @classmethod
    def setUpClass(cls):
        cls.actions = build_shortcut.build_main(debug=True).to_plist()["WFWorkflowActions"]

    def test_comment_shows_build_stamp(self):
        """第一个注释要带生成时间戳。

        事故（2026-11-12）：改完逻辑让用户重新导入，跑出来还是旧行为，
        分不清是"没改对"还是"手机上装的是旧的"。所以每次生成都把一个
        时间戳写进最上面的注释，导入后打开就能确认版本。
        """
        first = self.actions[0]
        self.assertEqual(first["WFWorkflowIdentifier"]
                         if "WFWorkflowIdentifier" in first
                         else first["WFWorkflowActionIdentifier"],
                         "is.workflow.actions.comment")
        text = first["WFWorkflowActionParameters"]["WFCommentActionText"]
        self.assertTrue(text.startswith("版本："), "第一个注释要以「版本：」开头")
        self.assertRegex(text.split("\n")[0], r"^版本：\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_normal_version_has_no_log_actions(self):
        """正常版不插任何断点弹窗，免得每天跑都要人点十几下。"""
        normal = build_shortcut.build_main().to_plist()["WFWorkflowActions"]
        ids = ids_of(normal)
        for noisy in ("is.workflow.actions.alert",
                      "is.workflow.actions.showresult",
                      "is.workflow.actions.setclipboard"):
            self.assertNotIn(noisy, ids, f"正常版不该有 {noisy}")

    def test_debug_stops_are_alerts(self):
        """诊断版用「显示提醒」当断点：点好继续、点取消终止。

        对应事故（2026-11-11/12）：把日志攒在变量里，真机上一直弹空白。
        改成每一步单独弹窗，不依赖变量跨块传递。
        """
        alerts = [a for a in self.actions
                  if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.alert"]
        self.assertGreaterEqual(len(alerts), 8, "诊断版应该有多处断点")
        for action in alerts:
            p = params(action)
            self.assertTrue(p.get("WFAlertActionTitle"), "断点要有标题")
            self.assertIn("WFAlertActionMessage", p, "断点要有内容")
            # 取消按钮要显示，用户才能在某一步停下
            self.assertTrue(p.get("WFAlertActionCancelButtonShown"),
                            "断点必须能取消，否则没法停在出问题的那一步")

    def test_debug_first_stop_shows_raw_json(self):
        """第一个断点显示解析出来的 JSON 原文，先确认数据源本身正常。"""
        first = [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.alert"][0]
        message = params(first)["WFAlertActionMessage"]["Value"]
        refs = [v for v in message.get("attachmentsByRange", {}).values()]
        self.assertTrue(refs, "第一个断点要引用真实动作输出，不能是死文本")
        # 应该是把响应正文取成文本（Get Text from Input）的那个动作
        outputs = {params(a).get("CustomOutputName"): a
                   for a in self.actions
                   if params(a).get("CustomOutputName")}
        self.assertIn("清单文本", outputs)
        self.assertEqual(outputs["清单文本"]["WFWorkflowActionIdentifier"],
                         "is.workflow.actions.detect.text")

    def test_no_stringified_dicts_anywhere(self):
        """整份 plist 里任何文本都不该出现被 str() 的字典。

        这条比只查某一种动作更宽：2026-11-12 同一个坑在「日志」和
        「显示日志」两处各踩了一次，现在换成弹窗又踩了一次。
        sk.text() 只认 WFTextTokenAttachment，遇到已拼好的 WFTextTokenString
        会 str() 出字典原文 —— 所以拼接一律走 merge()。
        """
        offenders = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")
            elif isinstance(node, str) and "WFSerializationType" in node:
                offenders.append(path)

        walk(self.actions, "actions")
        self.assertEqual(offenders, [],
                         "这些位置的文本里出现了字典原文：" + "; ".join(offenders))

    def test_debug_stops_are_plain_attachments(self):
        """弹窗内容的附件必须是裸引用，不能多包一层。"""
        for action in self.actions:
            p = params(action)
            if action["WFWorkflowActionIdentifier"] != "is.workflow.actions.alert":
                continue
            message = p.get("WFAlertActionMessage")
            if not isinstance(message, dict):
                continue
            self.assertEqual(message["WFSerializationType"], "WFTextTokenString")
            for ref in message["Value"].get("attachmentsByRange", {}).values():
                self.assertNotIn("WFSerializationType", ref,
                                 "附件不该再包 WFTextTokenAttachment 外壳")

    def test_debug_blocks_still_paired(self):
        """插断点不能把 If / Repeat 的括号搞坏。"""
        groups = {}
        for action in self.actions:
            p = params(action)
            if "GroupingIdentifier" in p:
                groups.setdefault(p["GroupingIdentifier"], []).append(
                    p.get("WFControlFlowMode"))
        for gid, modes in groups.items():
            self.assertEqual(modes[0], 0)
            self.assertEqual(modes[-1], 2)


class SelectionLogic(unittest.TestCase):
    """「挑哪一条班次」的判断链，对应事故：明明有班次却挑不到。"""

    @classmethod
    def setUpClass(cls):
        cls.actions = build_shortcut.build_main().to_plist()["WFWorkflowActions"]

    def test_reads_alarms_array(self):
        """取的是 alarms 数组，不是写死的某条班次（JSON 是快照，会过期）。"""
        keys = [params(a).get("WFDictionaryKey") for a in self.actions
                if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.getvalueforkey"]
        self.assertIn("alarms", keys)
        for dead in ("due", "next"):
            self.assertNotIn(dead, keys, f"{dead} 是构建时算的，会过期")

    def test_measures_gap_against_now(self):
        """用「现在」和班次时间做差，单位是秒。"""
        gaps = [a for a in self.actions
                if a["WFWorkflowActionIdentifier"] == A.GETTIMEBETWEENDATES]
        self.assertEqual(len(gaps), 1)
        p = params(gaps[0])
        self.assertEqual(p["WFTimeUntilUnit"], "Total Time")   # 秒
        self.assertEqual(
            p["WFTimeUntilFromDate"]["Value"]["attachmentsByRange"]["{0, 1}"]["Type"],
            "CurrentDate", "必须拿运行时当前时间比较，不能是发布时刻")

    def test_window_is_24_hours(self):
        """两条数值判断：还没到（>0）且 24 小时内（<=86400）。"""
        conds = []
        for a in self.actions:
            if a["WFWorkflowActionIdentifier"] != "is.workflow.actions.conditional":
                continue
            p = params(a)
            if "WFNumberValue" in p:
                conds.append((p["WFCondition"], p["WFNumberValue"]))
        self.assertIn((2, "0"), conds)                              # 大于 0：还没到
        self.assertIn((1, str(build_shortcut.DAY_SECONDS)), conds)   # 小于等于 86400
        self.assertEqual(build_shortcut.DAY_SECONDS, 24 * 3600)

    def test_no_variables_at_all(self):
        """全程不用变量。

        2026-11-12 真机实测：Set Variable 在 Repeat/If 块里写、块外读会得到空值，
        所以「已完成」这类跨块状态不能用。同一轮还试过「日期落进变量再取 .Time
        塞给 Add Alarm」，真机上仍卡在选择器里、闹钟没名字，于是连这唯一的变量
        也去掉了，改成把「格式化日期」的 HH:mm 文本直接交给 dateComponents。
        """
        for ident in ("is.workflow.actions.setvariable",
                      "is.workflow.actions.appendvariable"):
            self.assertEqual(
                [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == ident], [],
                "不要用变量，块内写块外读会得到空值")

    def test_alarm_time_is_formatted_hhmm_text(self):
        """dateComponents 传的是「格式化日期」出来的 HH:mm 文本，不是 .Time 属性。"""
        create = [a for a in self.actions
                  if a["WFWorkflowActionIdentifier"] == A.CLOCK_ADD_ALARM][0]
        body = params(create)["dateComponents"]["Value"]["attachmentsByRange"]
        (att,) = body.values()
        self.assertEqual(att["Type"], "ActionOutput",
                         "要直接引用「格式化日期」动作的输出，不再经过变量")
        self.assertEqual(att["OutputName"], "响铃时刻")
        self.assertNotIn("Aggrandizements", att,
                         "不要再取 .Time 属性，那条路真机卡在选择器里")
        # 被引用的动作必须是「格式化日期」，且格式串是 HH:mm
        fmt = [a for a in self.actions
               if a["WFWorkflowActionIdentifier"] == A.FORMAT_DATE]
        self.assertEqual(len(fmt), 1, "正式版只有一个「格式化日期」")
        p = params(fmt[0])
        self.assertEqual(p["WFDateFormat"], "HH:mm")
        self.assertEqual(p["WFDateFormatStyle"], "Custom")
        self.assertEqual(p["WFTimeFormatStyle"], "None")
        self.assertEqual(att["OutputUUID"], p["UUID"])

    def test_only_earliest_shift_is_handled(self):
        """一天里两条班次都落在窗口内时，只处理最早那条，避免建出一堆闹钟。"""
        creates = [a for a in self.actions
                   if a["WFWorkflowActionIdentifier"] == A.CLOCK_ADD_ALARM]
        self.assertEqual(len(creates), 1)
        toggles = [a for a in self.actions
                   if a["WFWorkflowActionIdentifier"] == A.CLOCK_TOGGLE_ALARM]
        self.assertEqual({params(t)["state"] for t in toggles}, {True, False})
        notes = [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.notification"]
        self.assertEqual(len(notes), 1, "新建成功后提示一句")
        self.assertEqual(
            len([a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == A.EXIT]), 2,
            "新建成功后必须也收工，否则会把明天的班次也建出来")


if __name__ == "__main__":
    unittest.main(verbosity=2)
