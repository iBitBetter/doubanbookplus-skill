# 豆瓣读书增强 · doubanbookplus

把任意形态的豆瓣读书输入（电脑端书页链接、移动端链接、书名、ISBN）归一化为可点击的各平台在线阅读直达链接，并一键生成统一风格的「阅读直达」HTML 网页。

> 示例产出见 [`result-三体.html`](result-三体.html) —— 这是用本 skill 为《三体》生成的阅读直达页样例。

## ✨ 功能特性

- **多形态输入**：豆瓣电脑端链接 / 移动端链接 / 书名 / ISBN，自动归一化。
- **多平台解析**：微信读书、得到、多看阅读、网易蜗牛读书、Z-Library、Anna's Archive（豆瓣阅读按需）。
- **智能匹配**：书名相似度 + 作者校验双重判定，规避「同名异书」误链。
- **统一风格 HTML**：生成深色 / 浅色自适应、带品牌色图标与底部署名的「阅读直达」网页，可截图、可分享、可嵌公众号。
- **双模式**：既能在 WorkBuddy 对话里 `@skill` 触发，也能纯命令行调用。

## 📦 安装

将本目录整体复制到 WorkBuddy 的 skills 目录下（二选一）：

```bash
# 用户级（所有项目可用）
~/.workbuddy/skills/doubanbookplus-skill/

# 项目级（仅当前项目）
<你的项目>/.workbuddy/skills/doubanbookplus-skill/
```

复制完成后，在 WorkBuddy 对话中输入 `@skill:doubanbookplus` 即可触发。

## 🚀 用法

### 方式一：对话中（推荐）

直接把需求说给助手听，例如：

- 给豆瓣链接：`@skill:doubanbookplus https://book.douban.com/subject/xxxx/`
- 只给书名：`解析《书名》各平台阅读直达链接`
- 生成网页：`把这本书做成「阅读直达」HTML 网页（豆瓣链接贴这）`
- 批量：`批量生成这几本书的阅读直达页：书名1 / 书名2 / 豆瓣链接…`

### 方式二：命令行

```bash
# 给豆瓣链接，输出 Markdown 直达链接
python scripts/resolve.py --url "https://book.douban.com/subject/xxxx/"

# 指定书名 / 作者 / ISBN（同名异书时补作者或 ISBN 更准）
python scripts/resolve.py --title "三体" --author "刘慈欣" --isbn "9787536692930"

# 生成固定风格 HTML 阅读直达页
python scripts/resolve.py --url "..." --html --output "书名-阅读直达.html"

# 只要 JSON 原始结果（便于二次处理）
python scripts/resolve.py --title "三体" --json
```

## ⚙️ 参数说明

| 参数 | 说明 |
|---|---|
| `--url` | 豆瓣书页链接（电脑端 / 移动端均可），**最推荐**，自动带书名·作者·ISBN |
| `--title` | 书名 |
| `--author` | 作者（同名异书时用于精确匹配） |
| `--isbn` | ISBN（最精确，优先于书名匹配） |
| `--html` | 生成固定风格 HTML 报告 |
| `--output` | HTML 输出路径（配合 `--html`） |
| `--json` | 只输出 JSON 原始结果 |

## 📚 平台与注意事项

各平台的搜索接口、直链格式、匹配阈值与已知限制，见 [`references/platforms.md`](references/platforms.md)：

- **微信读书 / 豆瓣阅读 / 得到 / 多看 / 蜗牛 / Z-Library / Anna's** 解析逻辑与踩坑笔记均已归档。
- 部分平台（如多看）在受限网络环境下可能因 SSL / 反爬返回「未找到」——属环境限制，非代码问题；脚本已内置「仅 SSL 错误时回退不校验」的兜底。
- 智能匹配的核心原则（含「作者缺失时子串必须降权」等）写在 `references/platforms.md` 的「智能匹配算法」一节，改动匹配逻辑时请勿回退。

## 🎨 自定义模板

- **HTML 样式**：集中在 [`templates/panel.html`](templates/panel.html)，改一处即可影响所有生成页面（封面、品牌色图标、徽章、深浅色、逐行动画、底部署名）。
- **平台图标 / 品牌色**：在 `scripts/resolve.py` 的 `PLATFORM_META` / `BRAND_COLORS` 中调整。
- **底部推广链接**：在 `templates/panel.html` 的 `.footer-promo` 区块修改。

## 🔧 依赖

- Python 3.x，**仅使用标准库**（`urllib` / `ssl` / `json` / `re`），无需 `pip install`。
- 运行环境需有出站网络。

## 📄 License

[MIT](LICENSE) © iBitBetter
