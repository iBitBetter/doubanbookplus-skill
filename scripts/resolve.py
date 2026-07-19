#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Douban eBook ++ — 解析脚本（skill 版）

把原 Chrome 扩展「豆瓣读书增强」的核心逻辑重写为可独立运行的 Python 脚本：
输入豆瓣书页链接 / 书名 / ISBN，直接调用各电子书平台 API，
解析出书籍详情页直达链接并输出 Markdown 列表。

设计原则（与原扩展一致）：
- 纯标准库，零三方依赖（urllib / re / json / hashlib / argparse）
- 无 CORS 限制（服务端请求，不需要浏览器跨域特权）
- 每个平台都有「搜索页兜底」与「未找到即不展示」语义
- 智能书名匹配：2-gram Jaccard 相似度 + 作者双向包含校验

用法：
    python resolve.py --url "https://book.douban.com/subject/12345/"
    python resolve.py --title "三体" --isbn "9787536692930" --author "刘慈欣"
    python resolve.py --title "三体" --json
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import ssl

# ============================================================
# 链接归一化（已整合进本技能，单一来源：scripts/douban_link.py）
# 提供 normalize_douban_url / search_douban_subject_by_isbn
# 支持电脑端 / 移动端链接 + ISBN 兜底搜索，详见 references/douban-api.md
# ============================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_douban_link():
    try:
        from douban_link import normalize_douban_url, search_douban_subject_by_isbn
        return normalize_douban_url, search_douban_subject_by_isbn
    except ImportError:
        sys.stderr.write(
            "错误：未找到本地归一化模块 scripts/douban_link.py。\n")
        sys.exit(3)


normalize_douban_url, search_douban_subject_by_isbn = _load_douban_link()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HTTP_TIMEOUT = 8          # 普通 API 请求超时（秒）
ZLIB_PROBE_TIMEOUT = 3    # Z-Library 镜像探活超时（秒）


# ============================================================
# 通用 HTTP 工具
# ============================================================

# 证书校验上下文：平时正常校验证书；仅当发生 SSL 证书错误时，
# 用下方不校验证书的上下文回退重试一次，兼顾安全性与沙盒/内网可用性。
_SSL_NO_VERIFY_CTX = ssl.create_default_context()
_SSL_NO_VERIFY_CTX.check_hostname = False
_SSL_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE


def _urlopen(req, timeout):
    """打开请求；遇 SSL 证书校验错误则回退到不校验证书重试一次。

    平时走系统默认上下文（正常校验证书），只有在证书真的连不上时
    （如沙盒 CA 缺失、内网自签证书）才放行，不削弱默认安全 posture。
    """
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except ssl.SSLError:
        sys.stderr.write("[SSL] 证书校验失败，回退不校验重试：%s\n" % req.get_full_url())
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_NO_VERIFY_CTX)
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
            sys.stderr.write("[SSL] 证书校验失败，回退不校验重试：%s\n" % req.get_full_url())
            return urllib.request.urlopen(req, timeout=timeout, context=_SSL_NO_VERIFY_CTX)
        raise


def http_get(url, headers=None, timeout=HTTP_TIMEOUT, data=None, method=None):
    """返回 (status_ok, text_or_dict)。失败返回 (False, None)。"""
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with _urlopen(req, timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            ctype = resp.headers.get("Content-Type", "")
            # 正常按 Content-Type 判断；部分接口返回 JSON 但 Content-Type 非 json
            # （多看阅读即此情况），对“看起来像 JSON”的响应体做一次兜底解析。
            if "application/json" in ctype or url.endswith(".json") or raw[:1] in "{[":
                try:
                    return True, json.loads(raw)
                except ValueError:
                    return True, raw
            return True, raw
    except urllib.error.HTTPError as e:
        # 仍尝试读取响应体（部分 API 在 4xx 时返回 JSON）
        try:
            raw = e.read().decode("utf-8", errors="ignore")
        except Exception:
            raw = ""
        if raw:
            try:
                return True, json.loads(raw)
            except ValueError:
                return False, None
        return False, None
    except Exception:
        return False, None


def http_head_alive(url, timeout=ZLIB_PROBE_TIMEOUT):
    """镜像探活：主机有 HTTP 响应即视为可达（含 403/503 等 bot 拦截），
    仅 DNS 失败 / 超时 / 连接拒绝等连接级错误才算不可用。
    这样在服务端被 Cloudflare 拦截时仍能展示镜像（用户浏览器通常可访问）。"""
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", UA)
    try:
        with _urlopen(req, timeout) as resp:
            return resp.status < 400
    except urllib.error.HTTPError:
        # 服务器有响应（403/503 bot 拦截或路径不存在）= 主机可达
        return True
    except Exception:
        return False


# ============================================================
# 微信读书 bookId 编码（MD5 + 自定义 hex 变换）
# 算法来源：微信读书前端 bookId → web URL ID 的编码流程
# ============================================================
def md5_hex(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def encode_weread_id(book_id):
    """将微信读书数字/字符串 bookId 编码为 web bookDetail URL 所需的 hex ID。"""
    s = md5_hex(str(book_id))
    str_sub = s[:3]

    if str(book_id).isdigit():
        chunks = []
        i, L = 0, len(book_id)
        while i < L:
            chunk = book_id[i:i + 9]
            chunks.append(format(int(chunk), "x"))
            i += 9
        fa = ["3", chunks]
    else:
        d = "".join(format(ord(ch), "x") for ch in book_id)
        fa = ["4", [d]]

    str_sub += fa[0]
    str_sub += "2" + s[-2:]

    for j, piece in enumerate(fa[1]):
        n = format(len(piece), "x")
        if len(n) == 1:
            n = "0" + n
        str_sub += n
        str_sub += piece
        if j < len(fa[1]) - 1:
            str_sub += "g"

    if len(str_sub) < 20:
        str_sub += s[:20 - len(str_sub)]

    str_sub += md5_hex(str_sub)[:3]
    return str_sub


# ============================================================
# 智能书名匹配
# ============================================================
_PUNCT_RE = re.compile(
    r'[\s\(\)\（\）\（《》「」『』\-,，。、：:；;！!？?""\'\'=\.]'
)


def _clean_title(s):
    return _PUNCT_RE.sub("", s)


def _strip_html(s):
    """去除 HTML 标签（如得到搜索返回的 <hl> 高亮标签），避免污染书名匹配。"""
    return re.sub(r"<[^>]+>", "", s or "")


def _core_author(a):
    """提取作者人名核心：去掉 [国家]/[译者] 等方括号前缀并去标点后转小写，
    避免「[俄罗斯] 果戈理」与「[俄] 果戈理」因国别缩写差异被误判为作者不符。"""
    a = re.sub(r"\[[^\]]*\]", "", a or "")
    return _clean_title(a).lower()


def title_similarity(a, b):
    """返回 0~1 的相似度：先比完全相等/包含关系，再算 2-gram Jaccard。"""
    if not a or not b:
        return 0.0
    a = a.strip().lower()
    b = b.strip().lower()
    if a == b:
        return 1.0
    ca, cb = _clean_title(a), _clean_title(b)
    if ca == cb:
        return 0.95
    if len(ca) > 1 and len(cb) > 1:
        # 目标被包含在更长的候选里（多为带副标题/版本号/译本的同一本书）
        if ca in cb:
            return 0.85
        # 候选是目标的更短子串（如「金钱」⊂「金钱心理学」）：不提前返回，
        # 落到下面的 2-gram Jaccard，短子串自然得低分，避免误匹配。

    def bigrams(s):
        d = {}
        for i in range(len(s) - 1):
            bg = s[i:i + 2]
            d[bg] = d.get(bg, 0) + 1
        return d

    ba, bb = bigrams(ca), bigrams(cb)
    keys = set(ba) | set(bb)
    inter = sum(min(ba.get(k, 0), bb.get(k, 0)) for k in keys)
    union = sum(max(ba.get(k, 0), bb.get(k, 0)) for k in keys)
    return inter / union if union else 0.0


def book_match(douban_title, douban_author, results, get_title, get_author,
               min_title_score=0.60):
    """对搜索结果做书名 + 作者交叉匹配，返回最佳匹配项。"""
    best = {"index": -1, "score": 0.0, "matched": False, "authorMismatch": False}

    for i, r in enumerate(results):
        result_title = get_title(r) if get_title else ""
        result_author = get_author(r) if get_author else ""
        title_score = title_similarity(douban_title, result_title)
        author_score = 0.0
        # 用「去方括号前缀 + 去标点」的人名核心比较，避免国别缩写差异
        # （如「[俄罗斯] 果戈理」vs「[俄] 果戈理」）被误判为作者不符。
        da_core = _core_author(douban_author)
        ra_core = _core_author(result_author)
        both_authors = bool(da_core and ra_core)

        if both_authors:
            if da_core == ra_core:
                author_score = 1.0
            elif da_core in ra_core or ra_core in da_core:
                author_score = 0.80

        composite = title_score
        if author_score > 0:
            composite = title_score * 0.7 + author_score * 0.3

        # 作者明确冲突（同名异书）的候选直接失去资格，
        # 避免它压过「作者匹配、书名略长」的正确条目（如《三体》vs《三体全集》）
        is_mismatch = both_authors and author_score == 0
        if is_mismatch:
            continue

        if composite > best["score"]:
            best = {
                "index": i,
                "score": composite,
                "matched": False,
                "authorMismatch": False,
            }

    if best["index"] >= 0 and best["score"] >= min_title_score:
        best["matched"] = True
    return best


# ============================================================
# 豆瓣页面信息提取
# ============================================================
def fetch_douban_book_info(url):
    """从豆瓣书籍详情页提取书名 / ISBN / 作者。"""
    ok, html = http_get(url, {"Accept": "text/html,application/xhtml+xml"})
    info = {"title": "", "isbn": "", "author": ""}
    if not ok or not html:
        return info

    # 书名：多种选择器兜底
    m = re.search(r'<h1[^>]*>\s*<span[^>]*property="v:itemreviewed"[^>]*>(.*?)</span>', html, re.S)
    if m:
        info["title"] = m.group(1).strip()
    else:
        m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        if m:
            info["title"] = m.group(1).strip()

    # #info 信息块
    m = re.search(r'id="info"[^>]*>(.*?)</div>', html, re.S)
    if m:
        block = m.group(1)
        text = re.sub(r"<[^>]+>", "", block)
        im = re.search(r'ISBN[:\s]*([\d\-Xx]+)', text)
        if im:
            info["isbn"] = re.sub(r"[-\s]", "", im.group(1)).strip()
        am = re.search(r'/author/[^"]*">([^<]+)</a>', block)
        if am:
            info["author"] = am.group(1).strip()

    return info


# normalize_douban_url / search_douban_subject_by_isbn 已整合进本技能
#（scripts/douban_link.py），于文件顶部统一导入，避免重复维护。

# ============================================================
# 各平台解析器
# 每个返回 {"url": str, "found": bool}
# ============================================================

def resolve_weread(title, isbn, author):
    if title:
        url = "https://weread.qq.com/web/search/global?keyword=" + urllib.parse.quote(title.strip())
        ok, data = http_get(url)
        books = (data or {}).get("books") or [] if isinstance(data, dict) else []
        if books:
            match = book_match(
                title, author, books,
                lambda b: (b.get("bookInfo") or {}).get("title", ""),
                lambda b: (b.get("bookInfo") or {}).get("author", ""),
                0.60,
            )
            if match["matched"]:
                bid = str(books[match["index"]]["bookInfo"]["bookId"])
                return {"url": "https://weread.qq.com/web/bookDetail/" + encode_weread_id(bid), "found": True}
    if isbn:
        url = "https://weread.qq.com/web/search/global?keyword=" + urllib.parse.quote(isbn)
        ok, data = http_get(url)
        books = (data or {}).get("books") or [] if isinstance(data, dict) else []
        if books:
            match = book_match(
                title, author, books,
                lambda b: (b.get("bookInfo") or {}).get("title", ""),
                lambda b: (b.get("bookInfo") or {}).get("author", ""),
                0.50,
            )
            if match["matched"]:
                bid = str(books[match["index"]]["bookInfo"]["bookId"])
                return {"url": "https://weread.qq.com/web/bookDetail/" + encode_weread_id(bid), "found": True}
    q = isbn or title or ""
    return {"url": "https://weread.qq.com/web/search?key=" + urllib.parse.quote(q), "found": False}


def resolve_duokan(title, isbn, author):
    q = isbn or title
    if not q:
        return {"url": "https://www.duokan.com/search/", "found": False}
    url = "https://www.duokan.com/target/search/web?s=" + urllib.parse.quote(q.strip()) + "&p=1"
    ok, data = http_get(url, {
        "Accept": "application/json",
        "Referer": "https://www.duokan.com/m/",
    })
    books = (data or {}).get("books") or [] if isinstance(data, dict) else []
    if books:
        match = book_match(title, author, books,
                           lambda b: b.get("title", ""),
                           lambda b: b.get("author", ""),
                           0.60)
        if match["matched"]:
            bid = books[match["index"]]["book_id"]
            return {"url": "https://www.duokan.com/reader/www/app.html?id=" + str(bid), "found": True}
    return {"url": "https://www.duokan.com/search/" + urllib.parse.quote(q.strip()), "found": False}


def resolve_dedao(title, author):
    if not title:
        return {"url": "https://www.dedao.cn/search", "found": False}
    url = "https://www.dedao.cn/api/search/pc/suggest"
    payload = json.dumps({"query": title.strip(), "searchType": 2}).encode("utf-8")
    ok, data = http_get(url, {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }, data=payload, method="POST")
    lists = (((data or {}).get("c") or {}).get("list") or []) if isinstance(data, dict) else []
    ebooks = []
    for grp in lists:
        for item in (grp.get("list") or []):
            if item.get("type") == 2 and item.get("extra") and item["extra"].get("enid"):
                ebooks.append(item)
    if ebooks:
        match = book_match(title, author, ebooks,
                           lambda b: _strip_html(b.get("title", "")),
                           lambda b: (b.get("extra") or {}).get("authorName", ""),
                           0.55)
        if match["matched"]:
            enid = ebooks[match["index"]]["extra"]["enid"]
            return {"url": "https://www.dedao.cn/ebook/reader?id=" + enid, "found": True}
    return {"url": "https://www.dedao.cn/search?keyword=" + urllib.parse.quote(title.strip()), "found": False}


def resolve_douban_read(title, author):
    if not title:
        return {"url": "https://read.douban.com/search", "found": False}
    url = "https://read.douban.com/j/search?query=" + urllib.parse.quote(title.strip())
    ok, data = http_get(url, {"Accept": "application/json, text/plain, */*"})
    if isinstance(data, list):
        ebooks = [d for d in data if d.get("type") == "ebook" and d.get("id")]
        if ebooks:
            match = book_match(title, author, ebooks,
                               lambda b: b.get("title", ""),
                               lambda b: b.get("author", ""),
                               0.55)
            if match["matched"]:
                return {"url": "https://read.douban.com/reader/ebook/" + str(ebooks[match["index"]]["id"]) + "/", "found": True}
    return {"url": "https://read.douban.com/search?q=" + urllib.parse.quote(title.strip()), "found": False}


def resolve_woniu(title, isbn, author):
    if not title:
        return {"url": "https://du.163.com/search", "found": False}
    url = "https://du.163.com/search/book.json?word=" + urllib.parse.quote(title.strip()) + "&page=1&pageSize=5"
    ok, data = http_get(url, {"Accept": "application/json"})
    if isinstance(data, dict) and data.get("code") == 0:
        wrappers = data.get("bookWrappers") or []
        if wrappers:
            match = book_match(title, author, wrappers,
                               lambda b: _strip_html((b.get("book") or {}).get("title", "")),
                               lambda b: (b.get("book") or {}).get("author", ""),
                               0.60)
            if match["matched"]:
                book = wrappers[match["index"]].get("book") or {}
                bid = book.get("bookId") or wrappers[match["index"]].get("bookId")
                return {"url": "https://du.163.com/share/book/" + str(bid), "found": True}
    return {"url": "https://du.163.com/search?keyword=" + urllib.parse.quote(title.strip()), "found": False}


# ============================================================
# Z-Library 镜像探活
# ============================================================
def _default_zlib_mirrors():
    return [{"name": "zlib.re", "homeUrl": "https://zh.zlib.re/", "searchBase": "https://zh.vbh101.ru"}]


def _load_zlib_mirrors(config_path=None):
    """优先从 JSON 配置文件读取镜像列表，否则用默认。"""
    if config_path:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                mirrors = json.load(f)
                if isinstance(mirrors, list) and mirrors:
                    return mirrors
        except Exception:
            pass
    return _default_zlib_mirrors()


def resolve_zlibrary(isbn, title, config_path=None):
    mirrors = _load_zlib_mirrors(config_path)
    for m in mirrors:
        search_query = isbn or title
        search_url = m["searchBase"]
        if search_query:
            search_url += "/s/" + urllib.parse.quote(search_query.strip())
        else:
            search_url += "/"
        if http_head_alive(search_url, ZLIB_PROBE_TIMEOUT):
            return {"url": search_url, "mirror": m["name"], "alive": True}
    # 全部不可达，兜底第一个
    fb = mirrors[0]
    q = isbn or title
    fallback_url = fb["searchBase"] + ("/s/" + urllib.parse.quote(q.strip()) if q else "/")
    return {"url": fallback_url, "mirror": fb["name"], "alive": False}


# ============================================================
# Anna's Archive（可配置搜索地址）
# ============================================================
def resolve_annas(title, isbn, base_url=None):
    if not base_url:
        base_url = "https://annas-archive.gl/search?q={query}"
    q = isbn or title or ""
    url = base_url.replace("{query}", urllib.parse.quote(q))
    return {"url": url, "found": True, "custom": bool(base_url != "https://annas-archive.gl/search?q={query}")}


# ============================================================
# 编排：解析全部平台
# ============================================================
def resolve_all(book_info, zlib_config=None, annas_base=None):
    title = book_info.get("title", "")
    isbn = book_info.get("isbn", "")
    author = book_info.get("author", "")

    results = []
    results.append(("微信读书", resolve_weread(title, isbn, author)))
    results.append(("豆瓣阅读", resolve_douban_read(title, author)))
    results.append(("得到", resolve_dedao(title, author)))
    results.append(("多看阅读", resolve_duokan(title, isbn, author)))
    results.append(("网易蜗牛读书", resolve_woniu(title, isbn, author)))
    results.append(("Z-Library", resolve_zlibrary(isbn, title, zlib_config)))
    results.append(("Anna's Archive", resolve_annas(title, isbn, annas_base)))
    return results


# ============================================================
# 输出格式化
# ============================================================
def format_markdown(book_info, results):
    title = book_info.get("title") or "(未知书名)"
    lines = []
    lines.append("# 《%s》在线阅读直达" % title)
    if book_info.get("author"):
        lines.append("作者：%s" % book_info["author"])
    if book_info.get("isbn"):
        lines.append("ISBN：%s" % book_info["isbn"])
    lines.append("")

    found = [(name, r) for name, r in results if r.get("found") or r.get("alive")]
    missing = [name for name, r in results if not (r.get("found") or r.get("alive"))]

    if found:
        lines.append("## 已找到")
        for name, r in found:
            lines.append("- **%s** → %s" % (name, r["url"]))
    else:
        lines.append("_未在各平台找到该书。_")

    if missing:
        lines.append("")
        lines.append("未找到（已隐藏）：%s" % "、".join(missing))
    return "\n".join(lines)


# ============================================================
# HTML 报告（按豆瓣面板风格渲染，模板见 templates/panel.html）
# ============================================================
PLATFORM_META = {
    "微信读书": {
        "badge": "直达",
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="24" height="24" rx="6" fill="#23ac3d"/>'
            '<text x="12" y="16.6" font-size="13" font-weight="700" text-anchor="middle" '
            'fill="#fff" font-family="-apple-system,Segoe UI,sans-serif">阅</text></svg>'
        ),
    },
    "豆瓣阅读": {
        "badge": "直达",
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="24" height="24" rx="6" fill="#34b27e"/>'
            '<text x="12" y="16.6" font-size="13" font-weight="700" text-anchor="middle" '
            'fill="#fff" font-family="-apple-system,Segoe UI,sans-serif">读</text></svg>'
        ),
    },
    "得到": {
        "badge": "直达",
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="24" height="24" rx="6" fill="#e8661b"/>'
            '<text x="12" y="16.6" font-size="13" font-weight="700" text-anchor="middle" '
            'fill="#fff" font-family="-apple-system,Segoe UI,sans-serif">得</text></svg>'
        ),
    },
    "多看阅读": {
        "badge": "直达",
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="24" height="24" rx="6" fill="#f5a623"/>'
            '<text x="12" y="16.6" font-size="13" font-weight="700" text-anchor="middle" '
            'fill="#fff" font-family="-apple-system,Segoe UI,sans-serif">多</text></svg>'
        ),
    },
    "网易蜗牛读书": {
        "badge": "直达",
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="24" height="24" rx="6" fill="#d9534f"/>'
            '<path d="M13.5 13 a3.6 3.6 0 1 1 -3.6 -3.6 a2.1 2.1 0 1 0 2.1 2.1" fill="none" '
            'stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>'
            '<path d="M10 13 q3.2 0.2 3.4 3.2 q0.1 2.8 -3.4 2.8" fill="none" '
            'stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>'
            '<line x1="10" y1="9.4" x2="10" y2="6" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/>'
            '<circle cx="10" cy="5.5" r="0.75" fill="#fff"/></svg>'
        ),
    },
    "Z-Library": {
        "badge": "镜像",
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="24" height="24" rx="6" fill="#4a90e2"/>'
            '<path d="M6.6 7.2 h10.8 M17.4 7.2 L6.6 16.8 M6.6 16.8 h10.8" fill="none" '
            'stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    },
    "Anna's Archive": {
        "badge": None,
        "svg": (
            '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
            '<defs><linearGradient id="aaGrad" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#2b9b8f"/><stop offset="1" stop-color="#1c6b63"/>'
            '</linearGradient></defs>'
            '<rect width="24" height="24" rx="6" fill="url(#aaGrad)"/>'
            '<path d="M13 4.6 C10.6 4.6 8.6 6.6 8.6 9 C8.6 10.2 8.1 11 7.4 11.6 '
            'C6.8 12 6.5 12.7 6.5 13.4 C6.5 14.3 7.2 15 8.1 15.3 '
            'C7.9 16 7.9 16.8 8.2 17.5 C8.6 18.6 9.7 19.3 10.8 19.3 H12 '
            'C13.7 19.3 15 18 15 16.3 V6.4 C15 5.3 14.2 4.6 13 4.6 Z" fill="#fff"/></svg>'
        ),
    },
}

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "panel.html",
)


def format_html(book_info, results):
    """渲染 HTML 报告：命中平台按豆瓣「在线阅读」面板风格展示，未命中隐藏。"""
    BRAND_COLORS = {
        "微信读书": "#23ac3d",
        "豆瓣阅读": "#34b27e",
        "得到": "#e8661b",
        "多看阅读": "#f5a623",
        "网易蜗牛读书": "#d9534f",
        "Z-Library": "#4a90e2",
        "Anna's Archive": "#7b68ee",
    }
    title = book_info.get("title") or "未知书名"
    author = book_info.get("author") or "未知"
    isbn = book_info.get("isbn") or "—"
    cover_glyph = title[0] if title != "未知书名" else "书"

    found = [(n, r) for n, r in results if r.get("found") or r.get("alive")]

    if found:
        rows = []
        for i, (name, r) in enumerate(found):
            meta = PLATFORM_META.get(name, {"icon": name[0], "color": "#888888", "badge": "直达", "svg": None})
            url = html.escape(r.get("url", ""), quote=True)
            badge_html = '<span class="badge">%s</span>' % meta["badge"] if meta.get("badge") else ""
            if meta.get("svg"):
                icon_html = '<div class="platform-icon">%s</div>' % meta["svg"]
            else:
                icon_html = '<div class="platform-icon" style="background:%s">%s</div>' % (
                    meta.get("color", "#888888"), meta.get("icon", name[0]))
            rows.append(
                '<a class="platform-item" href="%s" target="_blank" rel="noopener" style="--index:%d;--brand:%s">'
                '<div class="platform-left">'
                '%s'
                '<span class="platform-name">%s</span>'
                '</div>%s</a>' % (url, i, meta.get("color") or BRAND_COLORS.get(name, "#888888"), icon_html, name, badge_html)
            )
        rows_html = "\n".join(rows)
    else:
        rows_html = '<div class="empty">未在各平台找到该书，暂无可直达的阅读来源。</div>'

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        tpl = f.read()

    return (tpl
            .replace("{{TITLE}}", html.escape(title, quote=True))
            .replace("{{AUTHOR}}", html.escape(author, quote=True))
            .replace("{{ISBN}}", html.escape(isbn, quote=True))
            .replace("{{COVER_GLYPH}}", html.escape(cover_glyph, quote=True))
            .replace("{{COUNT}}", str(len(found)))
            .replace("{{ROWS}}", rows_html))


# ============================================================
# 入口
# ============================================================
def main(argv=None):
    parser = argparse.ArgumentParser(description="豆瓣读书 → 电子书平台直达链接解析")
    parser.add_argument("--url", help="豆瓣书籍详情页 URL：电脑端 book.douban.com/subject/* 或移动端 douban.com/doubanapp/dispatch/book/*")
    parser.add_argument("--title", help="书名（无 URL 时直接指定）")
    parser.add_argument("--isbn", help="ISBN（可单独使用：先在豆瓣搜索对应书籍再继续解析；也可作为 --title 的备用字段）")
    parser.add_argument("--author", help="作者（可选，提升匹配准确率）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出原始结果")
    parser.add_argument("--html", action="store_true", help="生成 HTML 报告（按豆瓣面板风格）")
    parser.add_argument("--output", help="HTML 报告输出文件路径（配合 --html）")
    parser.add_argument("--zlib-config", help="Z-Library 镜像列表 JSON 文件路径")
    parser.add_argument("--annas-base", help="Anna's Archive 搜索地址模板（含 {query}）")
    args = parser.parse_args(argv)

    book_info = {"title": args.title or "", "isbn": args.isbn or "", "author": args.author or ""}

    if args.url:
        _, web_url = normalize_douban_url(args.url)
        fetched = fetch_douban_book_info(web_url)
        # 命令行显式参数优先，未给则沿用页面提取
        book_info["title"] = args.title or fetched["title"]
        book_info["isbn"] = args.isbn or fetched["isbn"]
        book_info["author"] = args.author or fetched["author"]

    # ISBN 单独输入：先在豆瓣搜索对应书籍，找到则继续解析；无结果则无需处理
    if not book_info["title"] and not args.url and book_info["isbn"]:
        search_url = search_douban_subject_by_isbn(book_info["isbn"])
        if search_url:
            fetched = fetch_douban_book_info(search_url)
            book_info["title"] = args.title or fetched["title"]
            book_info["isbn"] = args.isbn or fetched["isbn"]
            book_info["author"] = args.author or fetched["author"]
        else:
            sys.stderr.write("未在豆瓣找到 ISBN %s 对应的书籍，无需处理。\n" % book_info["isbn"])
            return 0

    if not book_info["title"] and not book_info["isbn"]:
        sys.stderr.write("错误：需要提供 --url，或 --title/--isbn。\n")
        return 2

    results = resolve_all(book_info, args.zlib_config, args.annas_base)

    if args.json:
        print(json.dumps({
            "book": book_info,
            "platforms": [
                {"name": n, "url": r.get("url"), "found": bool(r.get("found") or r.get("alive")),
                 "mirror": r.get("mirror")}
                for n, r in results
            ],
        }, ensure_ascii=False, indent=2))
    elif args.html:
        html_out = format_html(book_info, results)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(html_out)
            sys.stderr.write("已写入 HTML 报告：%s\n" % args.output)
        else:
            print(html_out)
    else:
        print(format_markdown(book_info, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
