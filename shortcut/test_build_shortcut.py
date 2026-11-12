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

    def test_add_alarm_has_no_phantom_params(self):
        """Add Alarm 只能传它真有的参数。

        对应事故：「请为此操作中的每个参数选取值，然后终止了」——
        以前多传了 label 和 repeatSchedule，两者都不在参数表里。
        """
        adds = [a for a in self.actions
                if a["WFWorkflowActionIdentifier"] == A.CLOCK_ADD_ALARM]
        self.assertEqual(len(adds), 1, "应该正好有一个「添加闹钟」")
        p = params(adds[0])
        allowed = {"AppIntentDescriptor", "CustomOutputName", "GroupingIdentifier",
                   "OpenWhenRun", "UUID", "WFControlFlowMode", "allowsSnooze",
                   "dateComponents", "name", "repeats"}
        self.assertEqual(set(p) - allowed, set())
        for phantom in ("label", "repeatSchedule"):
            self.assertNotIn(phantom, p, f"{phantom} 不是 Add Alarm 的参数")
        # 不传 repeats 才是不重复（枚举里只有 sunday..saturday）
        self.assertNotIn("repeats", p)
        self.assertIn("dateComponents", p)
        self.assertIn("name", p)

    def test_clock_descriptors(self):
        """三个闹钟动作的 descriptor 都用 com.apple.clock。"""
        clock_actions = {A.CLOCK_ADD_ALARM, A.CLOCK_FIND_ALARMS, A.CLOCK_TOGGLE_ALARM}
        seen = set()
        for action in self.actions:
            if action["WFWorkflowActionIdentifier"] in clock_actions:
                desc = params(action)["AppIntentDescriptor"]
                self.assertEqual(desc["BundleIdentifier"], "com.apple.clock")
                self.assertEqual(desc["Name"], "Clock")
                seen.add(action["WFWorkflowActionIdentifier"])
        self.assertEqual(seen, clock_actions, "三个闹钟动作应全部出现")

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

    def test_find_alarms_has_input_and_filter(self):
        """每次 Find Alarms 都要给数据源和过滤器，否则等于没选值。

        现在有两处查找：① 列出所有「排班」闹钟（contains 99）；
        ② 按目标标签精确找同名闹钟（is 4）。两处的过滤值都必须齐全。
        """
        finds = [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == A.CLOCK_FIND_ALARMS]
        self.assertEqual(len(finds), 2)
        operators = []
        for find in finds:
            p = params(find)
            self.assertEqual(p["WFContentItemInputParameter"], "Library")
            templates = p["WFContentItemFilter"]["Value"]["WFActionParameterFilterTemplates"]
            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0]["Property"], "label")   # 小写
            self.assertTrue(templates[0]["Values"]["String"], "过滤值不能为空")
            operators.append(templates[0]["Operator"])
        self.assertEqual(operators, [99, 4])                      # contains / is

    def test_filter_can_reference_action_output(self):
        """同名判断用动作输出当过滤值，不靠变量（变量跨块会读到空）。"""
        finds = [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == A.CLOCK_FIND_ALARMS]
        exact = params(finds[-1])["WFContentItemFilter"]["Value"][
            "WFActionParameterFilterTemplates"][0]["Values"]["String"]
        self.assertEqual(exact["WFSerializationType"], "WFTextTokenString")
        self.assertTrue(exact["Value"]["attachmentsByRange"],
                        "精确查找的标签必须是动作输出，不能是写死的文本")

    def test_alarm_label_reads_entity_property(self):
        """读闹钟 label 要两层 aggrandizement，少第一层读到空值。"""
        attachments = []

        def walk(node):
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for action in self.actions:
            walk(params(action))
        # 直接在 plist 里找那个组合
        def find_coercions(node, out):
            if isinstance(node, dict):
                aggs = node.get("Aggrandizements")
                if isinstance(aggs, list):
                    out.append(aggs)
                for value in node.values():
                    find_coercions(value, out)
            elif isinstance(node, list):
                for value in node:
                    find_coercions(value, out)

        coercions = []
        find_coercions(self.actions, coercions)
        entity_chains = [
            aggs for aggs in coercions
            if any(a.get("CoercionItemClass") == build_shortcut.ALARM_ENTITY_CLASS
                   for a in aggs)]
        self.assertTrue(entity_chains, "没有任何一处把循环项转成 AlarmEntity")
        for aggs in entity_chains:
            self.assertTrue(
                any(a.get("PropertyName") == "label" for a in aggs),
                "转成 AlarmEntity 后没有紧接着取 label")


class Diagnostics(unittest.TestCase):
    """诊断版（--debug）。日志本身也可能写成坏结构，这里一并测。"""

    @classmethod
    def setUpClass(cls):
        cls.actions = build_shortcut.build_main(debug=True).to_plist()["WFWorkflowActions"]

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
        """一个 Set Variable 都不许有。

        2026-11-12 真机实测：iOS 26 上 Set Variable 写的变量在 Repeat/If
        块里写、块外读会得到空值。旧的「已完成」标志因此永远是空 → 循环体
        整段被跳过 → 一条班次都挑不到，还不报错。所以整套逻辑只用动作输出
        当引用，不用任何变量。
        """
        used = [a for a in self.actions
                if a["WFWorkflowActionIdentifier"] in
                ("is.workflow.actions.setvariable",
                 "is.workflow.actions.appendvariable")]
        self.assertEqual(used, [], "不要在快捷指令里用变量，改用动作输出")

    def test_each_shift_maintains_its_own_alarm(self):
        """窗口内每条班次各自找闹钟、各自开关或新建（不再只挑第一条）。"""
        creates = [a for a in self.actions
                   if a["WFWorkflowActionIdentifier"] == A.CLOCK_ADD_ALARM]
        self.assertEqual(len(creates), 1)
        toggles = [a for a in self.actions
                   if a["WFWorkflowActionIdentifier"] == A.CLOCK_TOGGLE_ALARM]
        self.assertEqual({params(t)["state"] for t in toggles}, {True, False})
        notes = [a for a in self.actions
                 if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.notification"]
        self.assertEqual(len(notes), 1, "新建成功后提示一句")


if __name__ == "__main__":
    unittest.main(verbosity=2)
