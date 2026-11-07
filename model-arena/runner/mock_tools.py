# -*- coding: utf-8 -*-
"""mock_tools.py - scenario-aware tool results for the D6 multi-round tool loop.

Why this exists: the first version returned one fixed string per tool name. It did
not matter which file read_file was pointed at - it always answered with the same
todo list. A capable model reads that result, sees it does not match the request
("this is a todo list, not a budget spreadsheet"), and skips the step entirely.
The evaluator then scored it as "forgot to call the tool", which was really a
property of the fake tool, not of the model.

So results are now derived from the tool AND its arguments:
  * read_file(path)      -> content shaped by the file extension and name
  * read_spreadsheet(p)  -> a spreadsheet-shaped table
  * read_logs(p)         -> a short log with an ERROR line worth finding
  * clean_data(p)        -> reports rows touched
  * run_analysis(in)     -> a small ranking
  * send_email(to,...)   -> acknowledges the actual recipient
  * write_file(path)     -> confirms the actual path
A result that plausibly answers the request lets the loop continue the way it
would against a real tool layer.
"""
import json
import os

NL = chr(10)


def _ext(path):
    return os.path.splitext(str(path or ""))[1].lower()


def _base(path):
    return os.path.basename(str(path or "")) or "file"


def _flag(name):
    """Small helper: is this tool known at all?"""
    return name in _KNOWN


_KNOWN = {
    "read_file", "write_file", "list_dir", "read_spreadsheet", "clean_data",
    "run_analysis", "read_logs", "search_records", "read_calendar", "get_calendar",
    "send_calendar_invite", "book_appointment", "book_room", "book_flight",
    "book_movie", "search_flights", "search_music_sheet", "search_restaurants",
    "get_weather", "get_balance", "get_phone_balance", "set_alarm", "track_order",
    "track_package", "list_customers", "send_email", "send_email_batch", "send_sms",
    "summarize", "extract_entities", "translate", "transcribe", "text_to_speech",
    "convert_format", "convert_pdf", "compress_folder", "compress_image",
    "download_video", "format_code", "generate_report", "shorten_url", "web_search",
}


def result_for(name, args=None):
    """Return a JSON string describing a plausible tool result for this call."""
    a = args if isinstance(args, dict) else {}
    n = name or ""
    return json.dumps({"tool": n, "result": _text(n, a)}, ensure_ascii=False)


def _text(name, a):
    path = a.get("path") or a.get("input") or a.get("source") or ""

    if name == "read_file":
        if "budget" in str(path).lower() or _ext(path) in (".xlsx", ".xls", ".csv"):
            return ("预算表片段:" + NL +
                    "科目,预算,实际" + NL +
                    "研发,1200000,1180000" + NL +
                    "市场,800000,760000" + NL +
                    "运营,450000,470000")
        if "todo" in str(path).lower() or path.endswith(".md"):
            return ("- [ ] 交季度报告 (P0)" + NL +
                    "- [ ] 回复客户邮件 (P1)" + NL +
                    "- [x] 归档旧纪要")
        if "contract" in str(path).lower() or "合同" in str(path):
            return ("采购合同(节选)" + NL +
                    "第一条 合同金额:人民币 1,200,000 元" + NL +
                    "第二条 生效日期:2026-03-01" + NL +
                    "第三条 交付期限:2026-06-30")
        return "%s 的内容(共 %d 行)" % (_base(path), 42)

    if name == "read_spreadsheet":
        return ("sheet=Sheet1 rows=120 cols=8" + NL +
                "地区,销售额" + NL + "华东,3000" + NL + "华南,1800" + NL +
                "华北,1500" + NL + "(前 10 行含空值与重复行)")

    if name == "read_logs":
        return ("2026-10-12 09:14:02 INFO  starting trainer" + NL +
                "2026-10-12 09:14:07 ERROR IndexError: list index out of range" + NL +
                "  at train.py line 88, in <module> data[0]" + NL +
                "2026-10-12 09:14:07 INFO  exit code 1")

    if name == "clean_data":
        return "已清洗:删除空行 6 行、去重 4 行,剩余 110 行"

    if name == "run_analysis":
        return "销售额 Top3:华东 3000 / 华南 1800 / 华北 1500"

    if name == "write_file":
        return "已写入 %s" % (_base(path) or "文件")

    if name == "list_dir":
        return "todo.md  notes.md  archive/  budget.xlsx"

    if name == "search_flights":
        return "CA1501 08:00 / MU5102 10:30 / HU7605 14:20"
    if name == "book_flight":
        return "已预订 %s" % (a.get("flight_no") or "CA1501")
    if name == "list_customers":
        return "cust1, cust2, cust3 (共 10 位)"
    if name == "send_email":
        return "邮件已发送至 %s" % (a.get("to") or "收件人")
    if name == "send_email_batch":
        return "已排队群发 %d 封" % len(a.get("recipients") or ["x"] * 10)
    if name == "send_sms":
        return "短信已发送至 %s" % (a.get("to") or "手机号")
    if name == "get_weather":
        return "%s:晴,12-20C" % (a.get("city") or "当地")
    if name == "convert_pdf":
        return "已转换 %s -> docx" % (_base(path) or "PDF")
    if name == "extract_entities":
        return "amount=1,200,000; date=2026-03-01"
    if name == "generate_report":
        return "已生成报告 %s" % (a.get("output") or "report.md")
    if name == "read_calendar":
        return "本周日程:周一 评审会;周三 项目同步;周五 复盘"
    if name == "get_calendar":
        return "今日 3 个会议:10:00 站会 / 14:00 需求评审 / 16:30 客户沟通"
    if name == "get_balance":
        return "余额 12,480.55 CNY"
    if name == "get_phone_balance":
        return "话费余额 38.50 CNY"
    if name == "track_order":
        return "订单 %s:已签收" % (a.get("order_id") or "SF1234567890")
    if name == "track_package":
        return "包裹 %s:运输中" % (a.get("tracking_no") or "SF9876543210")
    if name == "set_alarm":
        return "已设置闹钟 %s" % (a.get("time") or "")
    if name == "search_records":
        return "体检记录:最近一次 2025-08-12,三甲医院,结论正常"
    if name == "translate":
        return "翻译结果:The weather is nice today."
    if name == "summarize":
        return "要点:1) ... 2) ... 3) ..."
    if name == "web_search":
        return "搜索结果 3 条:量子计算入门(维基)/ 量子比特原理(科普)/ 入门路线(博客)"
    if name == "format_code":
        return "已格式化 %s" % (_base(path) or "代码")
    if name == "compress_folder":
        return "已压缩为 %s" % (a.get("output") or "dist.zip")
    if name == "compress_image":
        return "已压缩 %s" % (_base(path) or "图片")
    if name == "convert_format":
        return "已转换 %s -> %s" % (_base(path) or "文件", a.get("to") or "json")
    if name == "shorten_url":
        return "短链接:https://s.example/ab12"
    if name == "book_appointment":
        return "已预约 %s %s" % (a.get("service") or "服务", a.get("datetime") or "")
    if name == "book_room":
        return "已预订会议室 %s" % (a.get("room") or "A")
    if name == "book_movie":
        return "已预订《%s》%s %s" % (a.get("movie") or "电影", a.get("date") or "",
                                     a.get("time") or "")
    if name == "search_music_sheet":
        return "找到《%s》钢琴谱 2 份" % (a.get("title") or "曲目")
    if name == "search_restaurants":
        return "附近川菜馆 3 家:蜀香园 / 川味观 / 老码头"
    if name == "send_calendar_invite":
        return "已发送会议邀请至 %s" % (a.get("to") or "参会人")
    if name == "transcribe":
        return "转写文本(节选):今天主要讨论了三件事……"
    if name == "text_to_speech":
        return "已合成语音"
    if name == "download_video":
        return "已下载到 %s" % (a.get("output") or "本地")
    if name == "read_calendar":
        return "本周日程:周一 评审会;周三 项目同步;周五 复盘"

    # unknown tool: still answer in a plausible shape rather than a bare "OK",
    # so the model is never forced to give up just because the stub is empty
    return "%s 执行完成" % (name or "工具")


# Backwards-compatible view for anything still importing MOCK_TOOL_RESULTS
MOCK_TOOL_RESULTS = {k: _text(k, {}) for k in sorted(_KNOWN)}