# 豆瓣读书链接形态与搜索接口参考

本文件记录「douban-link-resolver」技能所依赖的豆瓣链接形态与 ISBN 搜索接口，
供维护与排错时参考。

## 1. 可识别的链接形态

| 形态 | 示例 | 书籍 ID 位置 |
|------|------|--------------|
| 电脑端详情页 | `https://book.douban.com/subject/38392174/` | 路径 `subject/{id}` |
| 移动端分发页 | `https://www.douban.com/doubanapp/dispatch/book/38392174` | 路径 `dispatch/book/{id}` |

- 书籍 ID 为纯数字（`\d+`）。
- 两种形态都归一化为电脑端 URL：`https://book.douban.com/subject/{id}/`
- 归一化只做正则提取，不发起网络请求（快、稳）。

## 2. 移动端链接的处理要点（踩坑）

- **不要依赖 HTTP 重定向**：移动端分发页在浏览器里通常 302 跳转到 `book.douban.com`
  的对应详情页，但在服务端 / 无头环境（Skill 在沙箱运行）中，重定向目标可能是
  频道聚合页而非具体书籍页，导致提取失败。
- 直接正则提取 `dispatch/book/(\d+)` 中的 ID，自己构造电脑端 URL，绕开重定向，
  最稳妥。这是 `normalize_douban_url()` 的做法。

## 3. ISBN 兜底搜索接口

当输入只有 ISBN 时，用搜索接口定位书籍详情页：

```
GET https://search.douban.com/book/subject_search?search_text={ISBN}
```

- `search_text` 需 `urllib.parse.quote` 编码。
- 返回的是 **HTML 搜索结果页**（不是 JSON）。
- 解析方式：正则提取页面中第一个 `https?://book\.douban\.com/subject/(\d+)`。
- **无结果判定**：页面中没有任何 `subject/` 链接 → 返回 `None`，上游据此「无需处理」。

## 4. 请求与反爬注意事项

- User-Agent 使用桌面版 Chrome（见 `scripts/douban_link.py` 的 `UA`），否则可能被拒。
- 豆瓣对自动化请求有频控，调用方应控制频率、遵守 `robots.txt`。
- 网络不可达（DNS 失败 / 超时 / 连接拒绝）时 `http_get` 返回 `(False, None)`，
  不会抛异常，调用方据此安全降级。

## 5. 与上游技能的衔接

归一化产出的 `web_url` 即为电脑端 subject 详情页，上游可做：
- 解析书名 / 作者 / ISBN（如 `doubanbookplus-skill` 的 `fetch_douban_book_info`）
- 书摘、笔记、封面、评分等元数据抓取

`resolve_douban_input()` 的返回结构：
```python
{"subject_id": "38392174",
 "web_url": "https://book.douban.com/subject/38392174/",
 "source": "url"}            # 或 "isbn_search" / None
```
