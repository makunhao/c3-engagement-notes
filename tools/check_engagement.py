#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_engagement.py —— C3 群内参与的「要素预检」脚本

作用：在把帖子/参与记录发出去之前，机械地检查它是否具备高质量参与所需的要素。
      把「自查」从"靠记性"变成"跑一遍脚本"。

对应 C3 评分维度：
  - participationQuality（提问有上下文 / 回复有证据 / 推动讨论）
  - substance（新信息 / 可执行结论）

--------------------------------------------------------------------
设计要点（这两条来自实战教训，不是拍脑袋）
--------------------------------------------------------------------
1. **类型感知**：不同类型的参与，需要的要素不同。
   - 提问类：必须有 上下文 + 已尝试 + 具体卡点（C3 验收要点）
   - 分享/结论类：必须有 上下文 + 证据/数字 + 可执行结论
   如果对所有帖子套用「提问类」门槛，会把分享帖误判为不合格，
   进而**诱导作者为了过检查而做无意义的工作**（目标被度量劫持）。

2. **显式元数据优先**：帖子用 `<!-- type: question|share|feedback -->` 声明类型。
   没有声明才退化为关键词启发式。因为一篇分享帖里只要出现「想请教群里的」，
   启发式就会把它误判成提问类——**显式元数据比启发式可靠**。

--------------------------------------------------------------------
用法：
    python check_engagement.py                    # 扫 posts/ 目录
    python check_engagement.py a.md b.md
"""

import sys
import re
import os
from pathlib import Path

# ----------------------------------------------------------------------
# 帖子类型 → 该类型「应具备」的要素集合
# ----------------------------------------------------------------------
TYPE_APPLICABLE = {
    "question": ["上下文", "已尝试", "已排除", "具体卡点",
                 "证据/数字", "最小复现", "明确诉求", "候选答案"],
    "share":    ["上下文", "证据/数字", "可执行结论"],
    "feedback": ["上下文", "证据/数字", "可执行结论"],
}

# 硬门槛：不满足即判不合格
HARD_GATES = {
    "question": ["上下文", "已尝试", "具体卡点"],
    "share":    ["上下文", "证据/数字", "可执行结论"],
    "feedback": ["上下文", "证据/数字", "可执行结论"],
}

TYPE_LABEL = {"question": "提问类", "share": "分享/结论类", "feedback": "回馈群结论类"}

# ----------------------------------------------------------------------
# 要素规则：名称 → (正则候选列表, 权重, 说明)
# 权重按「信息量」定：上下文/卡点/证据最重，诉求/候选答案次之
# ----------------------------------------------------------------------
RULES = {
    "上下文": (
        [r"上下文", r"背景", r"我在做", r"环境[:：]", r"版本", r"目标是"],
        18, "写清环境/版本/你在做什么/目标，而不是只丢一句报错"),
    "已尝试": (
        [r"已尝试", r"试过", r"尝试过", r"我(先|曾|反复)"],
        14, "说明你试过什么，让别人不必重复你的路"),
    "已排除": (
        [r"排除", r"确认不是", r"不是.{0,6}问题"],
        14, "比「已尝试」更重要：写清排除了什么、凭什么排除"),
    "具体卡点": (
        [r"具体卡点", r"卡点", r"卡在", r"根因", r"我(认为|怀疑)", r"阻塞"],
        18, "把问题收窄到「哪个环节」，而不是「就是不work」"),
    "证据/数字": (
        [r"\d+/\d+", r"\d{3,}", r"\d+\s*→\s*\d+", r"截图", r"日志", r"输出[:：]"],
        14, "贴数字、贴日志、贴截图——让群友能真的帮上忙"),
    "最小复现": (
        [r"最小复现", r"复现", r"```", r"repro"],
        10, "最少代码 + 最少输入，能一行命令跑起来最好"),
    "明确诉求": (
        [r"想请教", r"请问", r"问题[:：]", r"想(知道|问)", r"求(助|教)"],
        6, "说清要解法 / 要方案对比 / 要指出盲区"),
    "候选答案": (
        [r"我(目前|现在)的(改法|做法|方案|解法)", r"我的(改法|设想|候选|方案)",
         r"我打算", r"已修复", r"我选的"],
        6, "给出自己的候选答案——让讨论从「科普」变成「评审」"),
    "可执行结论": (
        [r"可执行结论", r"给群里的", r"结论[:：]", r"建议[:：]",
         r"清单", r"[cC]hecklist", r"落地", r"模板", r"三步"],
        12, "分享类帖子必须有可被别人直接使用的结论"),
}

# 负面信号（红线相关）：命中即扣分
NEGATIVE = [
    (r"^\s*(\+1|顶|沙发|同上|safa|路过|mark)\s*$", 30, "疑似灌水/无内容回复"),
    (r"^\s*(\[?(图片|表情|emoji)\]?)\s*$", 30, "疑似纯表情/纯图片"),
]

C_OK = "\033[92m"
C_BAD = "\033[91m"
C_WARN = "\033[93m"
C_DIM = "\033[90m"
C_END = "\033[0m"


def detect_type(text: str) -> str:
    """优先读显式标记 <!-- type: xxx -->；没有才退化为关键词启发式。"""
    m = re.search(r"<!--\s*type\s*:\s*(question|share|feedback)\s*-->", text)
    if m:
        return m.group(1)

    for t, pats in (
        ("feedback", [r"回馈", r"总结回馈", r"沉淀"]),
        ("question", [r"想请教", r"请问", r"求助", r"求教"]),
    ):
        if any(re.search(p, text) for p in pats):
            return t
    return "share"


def check_text(text: str):
    ptype = detect_type(text)
    applicable = TYPE_APPLICABLE[ptype]

    hits = {}
    for label, (patterns, weight, note) in RULES.items():
        hits[label] = any(re.search(p, text, re.MULTILINE) for p in patterns)

    total = sum(RULES[l][1] for l in applicable)
    score = sum(RULES[l][1] for l in applicable if hits[l])

    penalties = [(reason, p) for pat, p, reason in NEGATIVE
                 if re.search(pat, text, re.MULTILINE)]

    missing = [l for l in HARD_GATES[ptype] if not hits[l]]
    hard_ok = not missing

    pct = (score / total * 100) if total else 0
    return {
        "ptype": ptype, "applicable": applicable, "hits": hits,
        "score": score, "total": total, "pct": pct,
        "penalties": penalties, "missing": missing, "hard_ok": hard_ok,
    }


def medium_fit(text: str):
    """媒介适配检查 —— 由一次真实失败换来的规则。

    事实：我发过 5 篇 1700–2900 字的长帖到群，**零回应**。
    原因不是内容差，而是群聊单屏只有 200–400 字，长帖等于 5–10 屏，没人读完。
    教训：**媒介适配优先于内容完整。**

    实现：按 `---` 切成块（近似「一条消息」），报告最长的几块。
    超过 CHAT_LIMIT 的块，直接发到群聊里大概率不会被读完。
    """
    blocks = [b for b in re.split(r"\n\s*---\s*\n", text) if b.strip()]
    if not blocks:
        return None, []
    sizes = sorted(((len(re.sub(r"\s", "", b)), i) for i, b in enumerate(blocks)),
                   reverse=True)
    return sizes, blocks


def print_medium_fit(text: str):
    CHAT_LIMIT = 500          # 一个块超过这个字数，就不适合直接当群聊消息发
    sizes, _ = medium_fit(text)
    if not sizes:
        return
    top = sizes[:3]
    print("-" * 70)
    print(f"  媒介适配（群聊单屏约 200–400 字）")
    for n, idx in top:
        flag = f"{C_WARN}⚠ 偏长{C_END}" if n > CHAT_LIMIT else f"{C_OK}✓ 可发群{C_END}"
        print(f"    {flag}  最长的块 #{idx + 1}：{n} 字"
              f"  {C_DIM}(约 {max(1, round(n / 300))} 屏){C_END}")
    if top[0][0] > CHAT_LIMIT:
        print(f"  {C_WARN}⚠ 存在 {top[0][0]} 字的块——群聊里大概率没人读完。{C_END}")
        print(f"  {C_DIM}   建议：拆成 ≤200 字的短帖（先发钩子，有人接话再展开长文）{C_END}")


# ----------------------------------------------------------------------
# 互动追踪表专用检查
# ----------------------------------------------------------------------
# 「帖子要素门槛」不适用于互动追踪表：追踪表里没有"上下文/卡点"这类东西，
# 对它套用帖子门槛会得到无意义的红灯（59%），进而误导作者去"补要素"。
# ——这又是「度量误判就修度量」的一个实例。
# 追踪表该检查的是：**四栏是否留痕、闭环（被回应）是否形成**。
# ----------------------------------------------------------------------
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _table_rows(seg_lines):
    """取表格数据行（跳过表头与 |---|---| 分隔行）。"""
    rows = [ln for ln in seg_lines if ln.startswith("|")]
    out = []
    for r in rows:
        if re.match(r"^\|[\s\-:|]+\|$", r.strip()):
            continue
        out.append(r)
    return out[1:] if out else out          # 去掉第一行表头


def _section_rows(text, start_kw, end_kw=None):
    seg, on = [], False
    for ln in text.splitlines():
        st = ln.strip()
        is_head = st.startswith("##")
        if is_head and start_kw in st:
            on = True
            continue
        if on and is_head and (end_kw is None or end_kw in st):
            break
        if on:
            seg.append(st)
    return _table_rows(seg)


def is_tracking_table(path: str, text: str) -> bool:
    return ("互动追踪" in os.path.basename(path)
            or "我发出的（Outbound）" in text)


def check_tracking_table(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    s1 = _section_rows(text, "一、我发出的", "二、我回应别人的")
    s2 = _section_rows(text, "二、我回应别人的", "三、别人回应我的")
    s3 = _section_rows(text, "三、别人回应我的", "四、结论沉淀")
    s5 = _section_rows(text, "五、零回应记录", "5.1")

    sent = [r for r in s1 if DATE_RE.search(r) and "未发出" not in r]
    unsent = [r for r in s1 if "未发出" in r]
    replied = [r for r in s2 if ("未发生" in r or DATE_RE.search(r))]
    inbound = [r for r in s3 if "无" not in r]          # 含真实回应原文的行
    zero_rounds = [r for r in s5 if r.count("|") >= 5]

    print("=" * 70)
    print(f"文件：{os.path.basename(path)}")
    print(f"{C_DIM}识别为：互动追踪表（检查「四栏是否留痕」，而非帖子要素）{C_END}")
    print("-" * 70)
    print(f"  第一节 我发出的      ：已发出 {len(sent)} 行 / 未发出 {len(unsent)} 行")
    print(f"  第二节 我回应别人的  ：已填写 {len(replied)} 行")
    print(f"  第三节 别人回应我的  ：{C_OK if inbound else C_BAD}{len(inbound)} 行有真实回应{C_END}"
          f"  {C_DIM}← 验收要点「被至少 1 位同学/教师回应」{C_END}")
    print(f"  第五节 零回应记录    ：{len(zero_rounds)} 轮已留痕")
    print("-" * 70)

    ok_sent = len(sent) >= 5
    ok_reply = len(replied) >= 3
    ok_inbound = len(inbound) >= 1
    ok_zero = len(zero_rounds) >= 1

    print(f"  {'[√]' if ok_sent else '[×]'} 第一节 ≥5 行有日期的记录")
    print(f"  {'[√]' if ok_reply else '[×]'} 第二节 ≥3 行（主动回应）")
    print(f"  {C_OK if ok_inbound else C_BAD}{'[√]' if ok_inbound else '[×]'} "
          f"第三节 ≥1 行（被回应）——验收要点，没有就不算闭环{C_END}")
    print(f"  {'[√]' if ok_zero else '[×]'} 第五节 如实记录零回应")

    closed = ok_sent and ok_reply and ok_inbound
    print("-" * 70)
    if closed:
        print(f"  {C_OK}✓ 参与闭环已形成{C_END}")
    else:
        print(f"  {C_WARN}⚠ 未闭环：缺「被回应」记录。这一栏由他人决定，"
              f"如实标注为 0 即可，不要伪造。{C_END}")
    print("=" * 70)
    return {"file": os.path.basename(path), "tracking": True,
            "passed": closed, "pct": 100.0,
            "chars": len(re.sub(r"\s", "", text)),
            "max_block": 0, "type": "互动追踪表"}


def print_report(path: str):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"{C_BAD}[读取失败] {path}: {e}{C_END}")
        return None

    r = check_text(text)
    char_count = len(re.sub(r"\s", "", text))
    ptype = r["ptype"]

    print("=" * 70)
    print(f"文件：{os.path.basename(path)}")
    print(f"识别类型：{TYPE_LABEL[ptype]}    去空白字符数：{char_count}")
    print("-" * 70)

    for label, (patterns, weight, note) in RULES.items():
        applicable = label in r["applicable"]
        mark = f"{C_OK}[√]{C_END}" if r["hits"][label] else f"{C_BAD}[×]{C_END}"
        if applicable:
            print(f"  {mark} {label:<8} (+{weight})  {C_DIM}{note}{C_END}")
        else:
            print(f"  {C_DIM}·  {label:<8} (不计入)  {note}{C_END}")

    for reason, penalty in r["penalties"]:
        print(f"  {C_WARN}⚠ 负面信号：{reason} (-{penalty}){C_END}")

    print("-" * 70)
    gate = "/".join(HARD_GATES[ptype])
    if r["hard_ok"]:
        print(f"  硬门槛（{gate}）：{C_OK}通过{C_END}")
    else:
        print(f"  硬门槛（{gate}）：{C_BAD}不通过{C_END}  "
              f"{C_BAD}缺：{'、'.join(r['missing'])}{C_END}")

    if char_count < 120:
        print(f"  {C_WARN}⚠ 内容过短（<120 字），难以构成「有实质」的参与{C_END}")

    col = C_OK if r["pct"] >= 70 else C_BAD
    print(f"  要素完备度（仅计该类型适用项）：{col}"
          f"{r['score']} / {r['total']} = {r['pct']:.0f}%{C_END}"
          f"  {C_DIM}(≥70% 视为合格){C_END}")

    print_medium_fit(text)
    print()

    sizes, _ = medium_fit(text)
    max_block = sizes[0][0] if sizes else char_count

    return {"file": os.path.basename(path), "pct": r["pct"],
            "hard_ok": r["hard_ok"], "chars": char_count, "max_block": max_block,
            "type": TYPE_LABEL[ptype], "passed": r["hard_ok"] and r["pct"] >= 70}


def main():
    args = sys.argv[1:]
    if not args:
        here = Path(__file__).resolve().parent
        cand = sorted((here.parent / "posts").glob("*.md"))
        if not cand:
            print("用法：python check_engagement.py <帖子或记录文件...>")
            return 1
        args = [str(p) for p in cand]

    results = []
    for p in args:
        try:
            _txt = Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"{C_BAD}[读取失败] {p}: {e}{C_END}")
            continue
        if is_tracking_table(p, _txt):
            results.append(check_tracking_table(p))
        else:
            r = print_report(p)
            if r:
                results.append(r)

    posts = [r for r in results if not r.get("tracking")]
    tracks = [r for r in results if r.get("tracking")]

    print("=" * 70)
    print(f"{C_DIM}汇总{C_END}")
    print("-" * 70)
    for r in posts + tracks:
        flag = f"{C_OK}√{C_END}" if r["passed"] else f"{C_BAD}×{C_END}"
        if r.get("tracking"):
            print(f"  [{flag}] {r['file']:<38} {r['type']:<10} "
                  f"{C_DIM}（追踪表：见上方四栏留痕报告）{C_END}")
        else:
            chat = f"{C_WARN}长{C_END}" if r["max_block"] > 500 else f"{C_OK}短{C_END}"
            print(f"  [{flag}] {r['file']:<38} {r['type']:<10} "
                  f"{r['pct']:>3.0f}%  ({r['chars']} 字, 最长块 {r['max_block']} {chat})")

    if posts:
        n_pass = sum(1 for r in posts if r["passed"])
        n_hard = sum(1 for r in posts if r["hard_ok"])
        print("-" * 70)
        print(f"  帖子硬门槛通过：{n_hard} / {len(posts)}")
        print(f"  帖子合格（硬门槛 + ≥70%）：{n_pass} / {len(posts)}")

        if n_hard >= 1:
            print(f"  {C_OK}✓ 满足 C3 验收要点：「至少 1 次提问具备完整要素」{C_END}")
        else:
            print(f"  {C_BAD}× 不满足：需要至少 1 条三要素齐全的提问{C_END}")

        if n_pass >= 3:
            print(f"  {C_OK}✓ 满足 C3 验收要点：「至少 3 次参与有推动性质」{C_END}")
        else:
            print(f"  {C_WARN}⚠ 当前合格条目 {n_pass} 条，建议补至 ≥3 条{C_END}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
