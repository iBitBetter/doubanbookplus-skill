# 平台 API 逆向要点与踩坑笔记

本技能把「豆瓣读书增强」Chrome 扩展的核心逻辑重写为可独立运行的 Python 脚本。
以下为各平台 API 的逆向要点、匹配阈值与实战踩坑，作为技能运行的参考资产。

---

## 一、平台清单与解析方式

| 平台 | 解析方式 | 直达链接形态 | 状态判定 |
|------|---------|--------------|---------|
| 微信读书 | 书名 → bookDetail 直链 | `https://weread.qq.com/web/bookDetail/{encodedId}` | 智能匹配 |
| 豆瓣阅读 | 书名 → reader/ebook 直链 | `https://read.douban.com/reader/ebook/{id}/` | 智能匹配 |
| 得到 | 书名 → ebook/reader 直链 | `https://www.dedao.cn/ebook/reader?id={enid}` | 智能匹配 |
| 多看阅读 | 书名 → reader/app.html 直链 | `https://www.duokan.com/reader/www/app.html?id={book_id}` | 智能匹配 |
| 网易蜗牛读书 | 书名 → share/book 直链 | `https://du.163.com/share/book/{bookId}` | 智能匹配 |
| Z-Library | 镜像探活 + ISBN 搜索 | `https://{searchBase}/s/{ISBN}` | 镜像可达即展示 |
| Anna's Archive | ISBN / 书名搜索 | `{annas_base}?q={query}` | 始终展示（搜索入口） |

---

## 二、各平台 API 细节

### 微信读书
- **搜索**：`GET /web/search/global?keyword={书名}`，返回 `{books:[{bookInfo:{title,author,bookId}}]}`
- **编码**：`bookId` → MD5 → 自定义 hex 变换 → 3 字符校验码。`encode_weread_id()` 为纯 Python `hashlib` 实现，与扩展内 `utils/weread-encode.js` 完全一致。
- **直链**：`https://weread.qq.com/web/bookDetail/{encodedId}`
- **策略**：书名搜索（阈值 0.60）→ ISBN 搜索（阈值 0.50，放宽）

### 豆瓣阅读
- **搜索**：`GET /j/search?query={书名}`，返回数组，过滤 `type==="ebook"`
- **直链**：`https://read.douban.com/reader/ebook/{id}/`
- **阈值**：0.55（同源平台，放宽）

### 得到
- **搜索**：`POST /api/search/pc/suggest`，body `{"query":书名,"searchType":2}`
- **过滤**：`type===2` 且 `extra.enid` 存在
- **直链**：`https://www.dedao.cn/ebook/reader?id={enid}`
- **阈值**：0.55（suggest API 结果较少，放宽）

### 多看阅读
- **搜索**：`GET /target/search/web?s={书名}&p=1`（从 JS 源码逆向）
- **直链**：`https://www.duokan.com/reader/www/app.html?id={book_id}`
- **阈值**：0.60
- ⚠️ **服务端运行限制**：多看反爬较严，从服务器 IP 直接请求常被拒（返回非 200）。浏览器扩展用用户 IP 通常可过。技能环境若返回空，属正常网络限制，非代码问题。

### 网易蜗牛读书
- **搜索**：`GET /search/book.json?word={书名}&page=1&pageSize=5`
- **直链**：`https://du.163.com/share/book/{bookId}`
- **阈值**：0.60
- ⚠️ **仅支持书名搜索**，ISBN 无效；搜索结果为宽松匹配，前 5 条可能不含目标书，属正常。
- ⚠️ **不返回作者字段**：蜗牛所有搜索结果的 `author` 均为 `None`。这意味着 `book_match` 的「作者冲突硬约束」对这些结果**完全不生效**，匹配只能依赖书名相似度。因此蜗牛这类平台对 `title_similarity` 的「短子串降权」极度敏感——一旦子串给高分就会把弱相关短书名误判为同一本书（见踩坑笔记「作者缺失平台子串误匹配」）。

### Z-Library 镜像探活
- **默认镜像**：`zlib.re` → 探测 `https://zh.vbh101.ru/s/{ISBN}`
- **探活语义**：主机有 HTTP 响应（含 403/503 等 bot 拦截）即视为可达；仅 DNS 失败 / 超时 / 连接拒绝才算不可用。
  - 原因：服务端运行常被 Cloudflare 拦截返回 503，但用户浏览器通常可访问，故仍展示镜像搜索链接。
- 可用 `--zlib-config zlib_mirrors.json` 覆盖镜像列表，格式：`[{"name","homeUrl","searchBase"}]`。

### Anna's Archive
- **默认**：`https://annas-archive.gl/search?q={query}`
- 可用 `--annas-base "https://你的镜像/search?q={query}"` 覆盖（必须含 `{query}` 占位符）。
- 始终展示（作为通用搜索入口），不做命中校验。

---

## 三、智能匹配算法

`book_match()` 对每个平台的搜索结果做「书名 + 作者」交叉匹配：

1. `title_similarity(a, b)`：
   - 完全相等 → 1.0
   - 清洗后相等（去标点/空格）→ 0.95
   - **包含关系（不对称处理）**：
     - 目标被包进更长候选（如「金钱心理学」⊂「金钱心理学（新版）」，多为带副标题/版本号/译本的同一本书）→ **0.85**
     - 候选是目标的**短子串**（如「金钱」⊂「金钱心理学」）→ **不固定给高分**，落到下面的 2-gram Jaccard，短子串自然得低分（约 0.25），避免误匹配
   - 否则 2-gram Jaccard 重叠度（0~1）
2. 综合得分 `composite = title_score * 0.7 + author_score * 0.3`（有作者时）
3. **作者冲突硬约束**：当豆瓣作者与结果作者均非空且完全无包含关系（同名异书），该候选直接失去资格，避免压过「作者匹配、书名略长」的正确条目（如《三体》vs《三体全集》）。
4. 最终 `composite >= min_title_score` 才判定命中。
5. **⚠️ 作者缺失时子串必须降权（核心原则）**：当某平台搜索结果不返回作者字段（如网易蜗牛读书，`author=None`），`book_match` 的作者校验整段被跳过，匹配**完全依赖书名相似度**。此时 `title_similarity` 对「候选是目标的短子串」若给固定高分（旧逻辑 0.90），会把弱相关的短书名（「金钱」）误判为同一本书（「金钱心理学」）。故必须保留上述不对称处理——短子串走 2-gram Jaccard 自然降权。**今后改动 `title_similarity` 时，此不对称逻辑不得回退。**

各平台阈值：微信读书/多看/蜗牛 0.60、得到/豆瓣阅读 0.55、微信读书 ISBN 策略 0.50。

---

## 四、踩坑笔记（与原扩展一致 + 技能新增）

| 坑 | 现象 | 解决 |
|----|------|------|
| **CORS** | 浏览器内直接 fetch 被拒 | 服务端脚本无 CORS 限制，天然规避 |
| **同名异书误匹配** | 《永结无情游》周嘉宁 vs 戴小华 | `authorMismatch` 硬约束，冲突候选失去资格 |
| **书名略长被压** | 《三体》被同名异作者条目抢最佳位 | 作者冲突候选直接 `continue`，正确条目（作者匹配）胜出 |
| **Number 精度** | 蜗牛 bookId（19 位）被截断 | 优先取字符串字段 `book.bookId` |
| **Z-Library 503** | 服务端被 Cloudflare 拦截 | 主机有响应即视为可达，仍展示镜像 |
| **多看反爬** | 服务端 IP 请求被拒 | 环境限制，浏览器中正常；技能如实返回未找到 |
| **host 限制** | 新增镜像域名需白名单 | 技能无此限制，任意域名可探活 |
| **作者缺失平台子串误匹配** | 蜗牛不返回作者，「金钱」被误匹配为「金钱心理学」（实为另一本《金钱》，目标书未上架） | `title_similarity` 对「候选是目标短子串」不再固定 0.90，落到 2-gram Jaccard 自然降权；详见「智能匹配算法」第 5 条原则 |
