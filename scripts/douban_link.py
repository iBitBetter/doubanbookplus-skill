#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douban-link-resolver — 豆瓣读书输入归一化模块

把任意形态的豆瓣读书输入统一为「电脑端 subject 详情页 URL」，供上游工具
（电子书搜索、书摘抓取、元数据解析等）继续处理。

支持的三类输入：
  1. 电脑端链接  https://book.douban.com/subject/38392174/
  2. 移动端链接  https://www.douban.com/doubanapp/dispatch/book/38392174
  3. ISBN        （仅给 ISBN 时，用 search.douban.com 兜底搜索首个命中）

设计原则：
- 纯标准库，零三方依赖（urllib / re / json / argparse）
- 移动端链接不依赖重定向，直接提取书籍 ID 构造电脑端 URL
- ISBN 搜索无结果时返回 None，调用方据此「无需处理」

用法（作为库）：
    from douban_link import resolve_douban_input
    r = resolve_douban_input(url="https://www.douban.com/doubanapp/dispatch/book/38392174")
    # r = {"subject_id": "38392174", "web_url": "https://book.douban.com/subject/38392174/", "source": "url"}

用法（命令行）：
    python douban_link.py --url "https://www.douban.com/doubanapp/dispatch/book/38392174"
    python douban_link.py --isbn "9787536692930"
    python douban_link.py --isbn "9787536692930" --json
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HTTP_TIMEOUT = 8


# ============================================================
# 通用 HTTP GET（最小实现，保持零依赖）
# ============================================================
def http_get(url, headers=None, timeout=HTTP_TIMEOUT):
    """返回 (status_ok, text)。失败返回 (False, None)。"""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return False, None


# ============================================================
# 核心：链接形态归一化
# ============================================================
def normalize_douban_url(url):
    """把电脑端 / 移动端豆瓣书页链接统一为电脑端 subject 详情页 URL。

    返回 (subject_id, web_url)；无法识别书籍 ID 时返回 (None, 原 url)。
    支持：
      - 电脑端 https://book.douban.com/subject/38392174/
      - 移动端 https://www.douban.com/doubanapp/dispatch/book/38392174
    """
    if not url:
        return (None, url)
    m = re.search(r'subject/(\d+)', url)
    if m:
        sid = m.group(1)
        return (sid, "https://book.douban.com/subject/%s/" % sid)
    m = re.search(r'dispatch/book/(\d+)', url)
    if m:
        sid = m.group(1)
        return (sid, "https://book.douban.com/subject/%s/" % sid)
    return (None, url)


def search_douban_subject_by_isbn(isbn):
    """用 ISBN 在豆瓣搜索，返回首个命中书籍的 subject 详情页 URL；无结果返回 None。"""
    q = (isbn or "").strip()
    if not q:
        return None
    url = "https://search.douban.com/book/subject_search?search_text=" + urllib.parse.quote(q)
    ok, html = http_get(url, {"Accept": "text/html,application/xhtml+xml"})
    if not ok or not html:
        return None
    m = re.search(r'https?://book\.douban\.com/subject/(\d+)', html)
    if m:
        return "https://book.douban.com/subject/%s/" % m.group(1)
    return None


def resolve_douban_input(url=None, isbn=None):
    """归一化任意形态的豆瓣输入为电脑端 subject 详情页 URL。

    优先级：url（两种形态均可）> isbn 兜底搜索。
    返回 dict：{"subject_id": str|None, "web_url": str|None, "source": "url"|"isbn_search"|None}

    - 给了 url：归一化后直接返回（source="url"）
    - 仅给 isbn：搜豆瓣，搜到返回 subject url（source="isbn_search"），搜不到返回 None
    - 都没有：返回全 None（调用方应提示用户提供链接或 ISBN）
    """
    if url:
        sid, web_url = normalize_douban_url(url)
        if sid:
            return {"subject_id": sid, "web_url": web_url, "source": "url"}
        # 链接形态无法识别书籍 ID，仍原样交回调用方处理
        return {"subject_id": None, "web_url": web_url, "source": None}

    if isbn:
        found = search_douban_subject_by_isbn(isbn)
        if found:
            sid, web_url = normalize_douban_url(found)
            return {"subject_id": sid, "web_url": web_url, "source": "isbn_search"}
        return {"subject_id": None, "web_url": None, "source": None}

    return {"subject_id": None, "web_url": None, "source": None}


# ============================================================
# 命令行入口（便于直接测试或作为独立工具调用）
# ============================================================
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="把豆瓣链接 / ISBN 归一化为电脑端 subject 详情页 URL")
    parser.add_argument("--url", help="豆瓣书籍链接（电脑端或移动端）")
    parser.add_argument("--isbn", help="ISBN（仅给 ISBN 时自动搜索）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    if not args.url and not args.isbn:
        sys.stderr.write("错误：需提供 --url 或 --isbn。\n")
        return 2

    result = resolve_douban_input(url=args.url, isbn=args.isbn)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["web_url"]:
            print(result["web_url"])
            if result["source"] == "isbn_search":
                print("（来源：ISBN 搜索）", file=sys.stderr)
        else:
            print("未在豆瓣找到对应书籍，无需处理。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
