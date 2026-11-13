# Mac-list → GitHub Pages 静态站点

把 [qianguyihao/Mac-list](https://github.com/qianguyihao/Mac-list) 的 Markdown 清单转换成可公开访问的网站。
零第三方依赖：生成器只用 Python 标准库，产出的每个 HTML 都自带样式与数据，双击 `docs/index.html` 即可离线打开。

## 产物

| 路径 | 说明 |
| --- | --- |
| `docs/index.html` | 首页。8 大类 / 61 小节 / 304 条软件，支持即时搜索、标签筛选（荐 / 开源 / 免费 / 付费）、深浅色、键盘快捷键 |
| `docs/pages/p01.html … p24.html` | 24 篇使用技巧文档，带目录、上下篇导航、代码块与表格样式 |
| `docs/.nojekyll` | 告诉 GitHub Pages 跳过 Jekyll，避免中文路径与无 Front Matter 的 Markdown 被误处理 |
| `build.py` | 生成器。源 Markdown 有更新时重跑即可 |
| `.github/workflows/pages.yml` | 可选：push 时自动构建并部署（每周一额外重建一次） |
| `.gitignore` | 排除 `._*` / `.DS_Store` / `.cache/` |

源仓库默认 `master` 分支；原仓库 **没有 LICENSE**，也没有启用 Pages。

## 发布方式

### 方案 A：分支直传（最简单，约 2 分钟）

1. 在 GitHub 上 **Fork** `qianguyihao/Mac-list` 到自己账号。
2. 把本目录的 `docs/` 整个文件夹放进 fork 的仓库根目录，提交到 `master`。
3. 仓库 **Settings → Pages → Build and deployment**：
   - Source: `Deploy from a branch`
   - Branch: `master` / `/docs` → Save
4. 等待约 1 分钟，访问 `https://<你的用户名>.github.io/Mac-list/`。

> 若想用 `https://<用户名>.github.io/` 作为地址，把仓库名改成 `<用户名>.github.io`，并把 `docs/` 里的内容放到仓库根目录。

### 方案 B：GitHub Actions 自动构建（内容更新后自动同步）

1. Fork 原仓库。
2. 把 `build.py`、`docs/`、`.github/workflows/pages.yml`、`.gitignore` 放进 fork 根目录。
3. **Settings → Pages → Source** 选 `GitHub Actions`。
4. 之后每次 push 都会重新解析仓库里的 Markdown 并发布；workflow 里还配了每周一自动重建。
5. 想同步上游更新：在本地 `git remote add upstream https://github.com/qianguyihao/Mac-list.git`，`git fetch upstream && git merge upstream/master` 后 push。

### 方案 C：自定义域名

在 `docs/` 下新建 `CNAME`，内容写域名（如 `mac.example.com`），并在 DNS 添加指向 `<用户名>.github.io` 的 CNAME 记录；随后在 Pages 设置里填同一域名、开启 Enforce HTTPS。

## 本地重新构建

```bash
python3 build.py                          # 自动 clone 源仓库到 .cache/ 后构建
python3 build.py --source /path/to/Mac-list   # 用本地已有的源仓库
python3 build.py --source .               # 在 fork 出来的仓库根目录执行（workflow 用这个）
python3 build.py --refresh                # 强制重新拉取上游
python3 build.py --full                   # 不做合规过滤，1:1 完整镜像
python3 -m http.server 8000 -d docs      # 本地预览 http://localhost:8000
```

首页内所有数据以 JSON 内嵌在 HTML 中，文章页同样自带样式，**不依赖 CDN、不需要 Node、不需要构建工具**。

## 两个必须知道的前提

1. **授权**：原仓库没有声明开源许可证（GitHub API `license: null`）。公开仓库仅意味着可浏览、可 Fork（GitHub 服务条款范围内的使用），**不等于授权你把内容重新发布成一个独立站点**。个人学习/内部预览问题不大；若打算长期公开运营、挂广告或商用，建议先在原仓库提 Issue 联系作者「千古壹号」取得书面许可，并在页脚保留署名与原文链接（当前页脚已保留）。
2. **合规过滤**：默认构建会跳过原 README 的「科学上网（代理工具）/（服务）」两个小节，以及 `02-软件操作/科学上网.md` 这一篇。这是面向公开展示时的默认设置；如为完全私有的 1:1 归档，用 `--full` 关闭过滤。过滤规则集中在 `build.py` 顶部的 `EXCLUDE_GROUPS` / `EXCLUDE_DOCS`。

## 已知限制

- 原 README 中少量写法不规范的标记（如 `[[开源]][url]`）已在生成器中做归一化；其余描述文字保持原样，不做事实校验，软件状态、价格、链接有效性以各官网为准。
- 原文档里的图片都是外链（`img.smyhvae.com`），站点不托管图片；若源站失效会显示为裂图。
- 源文档中指向仓库内 `.md` 的相对链接会自动改写为原仓库的 GitHub 页面地址。
