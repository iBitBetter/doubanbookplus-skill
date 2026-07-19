---
name: doubanbookplus
description: 豆瓣读书增强技能——输入豆瓣书籍详情页链接（电脑端 book.douban.com/subject/* 或移动端 douban.com/doubanapp/dispatch/book/*）或书名/ISBN，调用各电子书平台 API 解析并输出微信读书、得到、豆瓣阅读、多看阅读、网易蜗牛读书、Z-Library、Anna's Archive 的直达阅读链接。触发场景：(1) 用户给出豆瓣书籍链接（电脑端 book.douban.com 或移动端 doubanapp/dispatch/book 链接）想找电子版，(2) 用户说「这本书在哪能看/读」「找《XXX》的电子版/直达链接」「豆瓣读书增强」「一键找阅读源」，(3) 用户给了书名/ISBN 想跨平台查询在线阅读地址。不用于普通网页搜索或图书购买比价（那是别的技能）。
---

# 豆瓣读书增强技能（Douban eBook ++）

把一本豆瓣书籍，解析成各大电子书平台的**直达阅读链接**。核心是一个零依赖的 Python 脚本 `scripts/resolve.py`，直接调用各平台搜索 API，用智能书名匹配找到准确书籍，输出 Markdown 直达列表。

与原 Chrome 扩展的区别：扩展在浏览器里注入 UI；本技能在对话中运行，给定输入即产出链接，无需浏览器插件。

## 默认提示词（开箱即用，复制任意一条即可触发）

下面这些话直接发给我就能用。最推荐第 1 条（给豆瓣链接，能自动拿到书名/作者/ISBN，命中最准）：

1. **给豆瓣电脑端链接，找全网阅读源**（最常用）
   > 解析这本书的豆瓣页：https://book.douban.com/subject/38392257/

2. **移动端链接同样支持**
   > 这本书在哪能看？https://www.douban.com/doubanapp/dispatch/book/38392257

3. **只给书名**
   > 找一下《外套》在微信读书、得到、多看阅读上的电子版直达链接

4. **只给 ISBN**
   > 用 ISBN 9787521785036 查这本书在哪些平台能在线读

5. **直接生成「阅读直达」网页（HTML，深色/浅色自适应）**
   > 把《金钱心理学》的各平台阅读链接做成阅读直达网页

6. **批量生成多本书的阅读直达页**
   > 把这几本《外套》《安定此心》《一句顶一万句》都生成阅读直达 HTML

7. **追问某个平台有没有上架**
   > 这本书在网易蜗牛读书 / 多看阅读 上有电子版吗？

> 提示：给链接最省事；只给书名时若命中不准（同名异书），补一句作者或 ISBN 即可精准锁定。

## 内置模块

链接归一化（电脑端/移动端链接识别、ISBN 兜底搜索）**已整合进本技能**，单一来源为 `scripts/douban_link.py`，无需外部技能依赖。`scripts/resolve.py` 会从同目录直接导入 `normalize_douban_url` / `search_douban_subject_by_isbn`；若该文件缺失，脚本会打印提示并以退出码 3 结束。模块设计、链接形态与踩坑笔记见 `references/douban-api.md`。

> 本技能现已完全自包含：分发时连同 `scripts/douban_link.py` 与 `references/douban-api.md` 一起即可，不需要额外安装 douban-link-resolver。

## 工作流

### 0. 输入归一化

用户可能给三类输入，按需取：

- **豆瓣书页链接**（两种格式都支持，脚本自动识别）：
  - 电脑端：`https://book.douban.com/subject/38392174/`
  - 移动端：`https://www.douban.com/doubanapp/dispatch/book/38392174`（脚本提取书籍 ID 后统一走电脑端解析）
  - 最推荐，能自动拿到书名/ISBN/作者
- **书名 + 作者**：`--title "三体" --author "刘慈欣"`
- **ISBN**（可单独使用）：`--isbn "9787536692930"`。单给 ISBN 时，脚本会先访问 `https://search.douban.com/book/subject_search?search_text={ISBN}` 搜出对应书籍再继续解析；若搜不到结果则直接结束（无需处理）。配合书名时 ISBN 仅作备用字段。

什么都不给时，向用户索要书名或豆瓣链接（电脑端/移动端均可）。

### 1. 运行解析脚本

用本技能目录下的 `scripts/resolve.py`（绝对路径调用）。**脚本位置**：本 SKILL.md 同级 `scripts/resolve.py`。

```bash
# 方式 A：给豆瓣链接（电脑端 / 移动端均可，自动提取书名/ISBN/作者）
python <SKILL_ROOT>/scripts/resolve.py --url "https://book.douban.com/subject/2567698/"
# 移动端链接同样支持：
python <SKILL_ROOT>/scripts/resolve.py --url "https://www.douban.com/doubanapp/dispatch/book/38392174"

# 方式 A'：只给 ISBN（脚本先在豆瓣搜出书籍再解析；无结果则结束）
python <SKILL_ROOT>/scripts/resolve.py --isbn "9787536692930"

# 方式 B：直接给书名/作者/ISBN
python <SKILL_ROOT>/scripts/resolve.py --title "三体" --author "刘慈欣" --isbn "9787536692930"

# 方式 C：只要 JSON 原始结果（便于进一步处理）
python <SKILL_ROOT>/scripts/resolve.py --title "三体" --json

# 方式 D：输出固定 HTML 报告（推荐）——生成「阅读直达」网页，风格与 *-阅读直达.html 完全一致
python <SKILL_ROOT>/scripts/resolve.py --url "https://book.douban.com/subject/2567698/" --html --output "阅读直达.html"
# 只给书名/ISBN 也能生成 HTML：
python <SKILL_ROOT>/scripts/resolve.py --title "三体" --isbn "9787536692930" --html --output "三体-阅读直达.html"
```

可选覆盖参数：
- `--zlib-config <path.json>`：Z-Library 镜像列表（格式见 references/platforms.md）
- `--annas-base "https://你的镜像/search?q={query}"`：Anna's Archive 自定义搜索地址（必须含 `{query}`）

> 运行环境需有出站网络。多数平台从服务端可直接调用；多看阅读、Z-Library 等服务端可能被反爬/Cloudflare 拦截（见 references/platforms.md），此时脚本如实返回「未找到」，属正常网络限制，非代码问题。

### HTML 报告（固定模板，推荐）

加 `--html` 会按本技能内置的**固定模板** `templates/panel.html` 渲染一份「阅读直达」网页：封面书脊 + 书名首字 glyph、各平台品牌色 SVG 图标、直达/镜像徽章、深浅色一键切换、逐行淡入动画、底部署名。输出与你目录里已有的 `*-阅读直达.html` **风格完全一致**，可直接预览或分享，无需再手动排版。

- `--output <path.html>`：指定输出文件；省略则打印到 stdout。
- 模板占位符（`{{TITLE}}` / `{{AUTHOR}}` / `{{ISBN}}` / `{{COVER_GLYPH}}` / `{{COUNT}}` / `{{ROWS}}`）由 `resolve.py` 的 `format_html()` 自动填充；改样式只改 `templates/panel.html` 一处即可。
- 各平台图标与品牌色在 `resolve.py` 的 `PLATFORM_META` / `BRAND_COLORS` 中维护。

### 2. 解读输出

脚本输出 Markdown：

- `## 已找到`：各平台**直达链接**（命中书籍的详情页）。这些才是用户要的答案。
- `未找到（已隐藏）：…`：未命中或不可达的平台（按需求「无书的平台不展示」，最终回复里可省略此行）。

命中判定：5 个智能平台靠书名+作者匹配；Z-Library 靠镜像可达；Anna's Archive 始终展示（搜索入口）。

### 3. 输出给用户

把 `## 已找到` 的直达链接整理后回复用户（平台名 + 可点击链接）。可附一句：未命中的平台已省略。

若用了 `--html`，直接把生成的 HTML 文件作为可预览产物交付（用 present_files 打开预览即可），不要再去手搓网页——模板已与该系列文件风格统一。

示例回复：

> 《三体》刘慈欣 的在线阅读直达：
> - 微信读书：https://weread.qq.com/web/bookDetail/ce03...
> - 豆瓣阅读：https://read.douban.com/reader/ebook/594929245/
> - 得到：https://www.dedao.cn/ebook/reader?id=...
> - Z-Library：https://zh.vbh101.ru/s/9787536692930
> - Anna's Archive：https://annas-archive.gl/search?q=9787536692930

## 智能匹配说明（决定命中准确度）

- 书名 2-gram Jaccard 相似度 + 作者双向包含校验，综合分 ≥ 阈值（0.50~0.60）才命中。
- **作者冲突硬约束**：同名异书（如《永结无情游》周嘉宁 vs 戴小华）直接排除，不会误链。
- 详细算法与阈值见 `references/platforms.md`。

## 平台清单

微信读书、豆瓣阅读、得到、多看阅读、网易蜗牛读书（智能直达）；Z-Library（镜像探活）；Anna's Archive（可配置搜索）。

## 参考资料

- `references/platforms.md`：各平台 API 逆向要点、匹配阈值、踩坑笔记（含服务端运行限制）。
- `references/douban-api.md`：链接形态（电脑端/移动端）、ISBN 搜索接口、归一化踩坑笔记（内置模块 `scripts/douban_link.py` 的设计说明）。
