#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mac-list -> GitHub Pages 静态站点生成器（零第三方依赖，仅用 Python 标准库）

用法:
    python3 build.py                     # 自动 clone 源仓库到 .cache/ 后构建
    python3 build.py --source .          # 以当前目录（fork 出来的仓库根目录）为源
    python3 build.py --source /path/to/Mac-list --out ./docs
    python3 build.py --full              # 完整镜像（默认会过滤「科学上网」相关章节）
    python3 build.py --refresh           # 强制重新拉取源仓库

产物: docs/  —— 可直接交给 GitHub Pages（Settings > Pages > Deploy from a branch > /docs）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
REPO_URL = "https://github.com/qianguyihao/Mac-list.git"
ORIGIN_REPO = "https://github.com/qianguyihao/Mac-list"
BLOB_BASE = ORIGIN_REPO + "/blob/master/"
RAW_BASE = "https://raw.githubusercontent.com/qianguyihao/Mac-list/master/"

# 面向公开展示时的合规过滤：默认不收录代理工具 / 代理服务章节及对应文章。
# 加 --full 可关闭过滤，做 1:1 完整镜像。
EXCLUDE_GROUPS = {"科学上网（代理工具）", "科学上网（服务）"}
EXCLUDE_DOCS = {"科学上网.md"}

FOLDER_LABELS = {
    "01-新手指南": "新手指南",
    "02-其他软件": "软件专题",
    "02-软件操作": "操作与技巧",
    "03-推荐资源": "推荐资源",
}
FOLDER_ORDER = list(FOLDER_LABELS.keys())

# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------

LINK_RE = re.compile(r"\[([^\[\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_RE = re.compile(r"!\[([^\[\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
MD_LINK_RE = re.compile(r"\.md(?:#.*)?$")


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def normalise_brackets(s: str) -> str:
    """源文档里存在 [[开源]](url)、[[开源](url)]、[[开源][url] 等非标准写法。"""
    s = re.sub(r"\[\[([^\[\]]+)\]\]\(([^)\s]+)\)", r"[\1](\2)", s)
    s = re.sub(r"\[\[([^\[\]]+)\]\]\[([^\]\s]+)\]", r"[\1](\2)", s)
    s = re.sub(r"\[\[([^\[\]]+)\]\(([^)\s]+)\)\]", r"[\1](\2)", s)
    s = re.sub(r"\[\[([^\[\]]+)\]\(([^)\s]+)\)", r"[\1](\2)", s)
    return s


def resolve_url(url: str, base: str = "") -> str:
    url = url.strip()
    if url.startswith(("http://", "https://", "//", "mailto:", "data:", "#", "tel:")):
        return url
    if url.startswith("/"):
        return RAW_BASE.rstrip("/") + url
    path = base + url
    if "#" in path:
        path, anchor = path.split("#", 1)
        suffix = "#" + anchor
    else:
        suffix = ""
    if MD_LINK_RE.search(path):
        return BLOB_BASE + quote(path, safe="/") + suffix
    return RAW_BASE + quote(path, safe="/") + suffix


def inline_md(s: str, base: str = "") -> str:
    """行内 Markdown -> HTML（链接 / 图片 / 粗体 / 斜体 / 行内代码 / 删除线）。"""
    s = normalise_brackets((s or "").replace("\x00", ""))
    tokens: list[str] = []

    def stash(fragment: str) -> str:
        tokens.append(fragment)
        return "\x00%d\x00" % (len(tokens) - 1)

    def img_sub(m):
        alt, url = m.group(1), m.group(2)
        return stash('<img src="%s" alt="%s" loading="lazy">' % (esc(resolve_url(url, base)), esc(alt)))

    s = IMG_RE.sub(img_sub, s)

    def link_sub(m):
        text, url = m.group(1), m.group(2)
        inner = esc(text)
        inner = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", inner)
        return stash('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                     % (esc(resolve_url(url, base)), inner or "#"))

    s = LINK_RE.sub(link_sub, s)

    # 行内代码先保护起来，避免其中的 * 被当成粗体
    s = re.sub(r"`([^`]+)`", lambda m: stash("<code>%s</code>" % esc(m.group(1))), s)
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"~~(.+?)~~", r"<del>\1</del>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    def restore(m):
        k = int(m.group(1))
        return tokens[k] if 0 <= k < len(tokens) else m.group(0)
    s = re.sub(r"\x00(\d+)\x00", restore, s)
    return s.strip()


def build_list(rows, idx: int = 0, indent: int = 0):
    """rows: [(indent, text, ordered)] -> (html, next_index)"""
    tag = "ol" if rows[idx][2] else "ul"
    out = ["<%s>" % tag]
    n = len(rows)
    while idx < n:
        ind, text, ordered = rows[idx]
        if ind < indent:
            break
        if ind > indent:
            sub, idx = build_list(rows, idx, ind)
            out.append(sub)
            continue
        out.append("<li>%s" % inline_md(text, BASE_FOR_BLOCK))
        idx += 1
        if idx < n and rows[idx][0] > ind:
            sub, idx = build_list(rows, idx, rows[idx][0])
            out.append(sub)
        out.append("</li>")
    out.append("</%s>" % tag)
    return "".join(out), idx


BASE_FOR_BLOCK = ""


def md_to_html(text: str, base: str = "") -> str:
    """块级 Markdown -> HTML。覆盖标题/列表/引用/围栏代码/表格/分割线/图片/段落。"""
    global BASE_FOR_BLOCK
    BASE_FOR_BLOCK = base
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush():
        if para:
            joined = " ".join(x.strip() for x in para if x.strip())
            if joined:
                out.append("<p>%s</p>" % inline_md(joined, base))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush()
            lang = stripped[3:].strip().split()[0] if len(stripped) > 3 else ""
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = ' class="language-%s"' % esc(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, esc("\n".join(buf))))
            continue

        if not stripped:
            flush()
            i += 1
            continue

        hm = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if hm:
            flush()
            level = len(hm.group(1))
            title = inline_md(hm.group(2).strip(), base)
            anchor = "sec-%d" % len([x for x in out if x.startswith("<h")])
            out.append('<h%d id="%s">%s</h%d>' % (level, anchor, title, level))
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline_md(" ".join(x.strip() for x in buf), base))
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            flush()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "".join("<th>%s</th>" % inline_md(c, base) for c in header)
            tbody = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline_md(c, base) for c in row) for row in body)
            out.append('<div class="table-wrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                       % (thead, tbody))
            continue

        lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if lm:
            flush()
            rows = []
            while i < len(lines):
                m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if not m:
                    break
                rows.append((len(m.group(1).replace("\t", "    ")), m.group(3),
                             bool(re.match(r"\d", m.group(2)))))
                i += 1
            block, _ = build_list(rows, 0, rows[0][0])
            out.append(block)
            continue

        # 独立成行的图片
        if IMG_RE.fullmatch(stripped):
            flush()
            out.append('<figure>%s</figure>' % inline_md(stripped, base))
            i += 1
            continue

        para.append(line)
        i += 1

    flush()
    return "\n".join(out)


# ----------------------------------------------------------------------------
# README 解析
# ----------------------------------------------------------------------------

TAG_RULES = [
    ("荐", re.compile(r"【荐】|\[荐\]|（荐）|\(荐\)")),
    ("hot", re.compile(r"\[hot\]|【hot】", re.I)),
    ("开源", re.compile(r"开源")),
    ("免费", re.compile(r"免费")),
    ("付费", re.compile(r"付费")),
]
STRIP_TAGS = re.compile(r"【荐】|\[荐\]|（荐）|\(荐\)|\[hot\]|【hot】", re.I)
SEPARATOR_RE = re.compile(r"^\s*[-–—:：,，]+\s*")


def cid(s: str) -> str:
    return "id-" + re.sub(r"[^0-9a-zA-Z]+", "-", s).strip("-").lower() or "id-x"


def parse_item(body: str):
    """把 '- [名字](链接) - 描述' 解析为结构化条目。"""
    body = normalise_brackets(body.strip())
    first = LINK_RE.match(body)
    if first:
        name = first.group(1).strip()
        url = resolve_url(first.group(2))
        rest = body[first.end():]
    else:
        parts = re.split(r"\s+[-–—]\s+|\s*[：:]\s*", body, maxsplit=1)
        name = parts[0].strip()
        url = ""
        rest = parts[1] if len(parts) > 1 else ""
    rest = SEPARATOR_RE.sub("", rest, count=1).strip()
    name = re.sub(r"\*\*|`", "", name).strip()
    if not name:
        return None

    desc = STRIP_TAGS.sub("", rest).strip()
    # 剥离【荐】等标记后常留下重复标点，做一次轻量清理
    desc = re.sub(r"\s{2,}", " ", desc)
    desc = re.sub(r"[。！？]{2,}", lambda m: m.group(0)[0], desc)
    desc = re.sub(r"[，,、]\s*[。！？]", lambda m: m.group(0)[-1], desc)
    desc = re.sub(r"[。！？]\s*[，,]", lambda m: m.group(0)[0], desc)
    badges = [label for label, rx in TAG_RULES if rx.search(rest) or rx.search(name)]
    return {
        "n": name,
        "u": url,
        "d": inline_md(desc),
        "b": badges,
        "no": [],
        "c": [],
    }


def parse_readme(text: str, full: bool):
    cats: list[dict] = []
    cur_cat = None
    cur_group = None
    last_item = None
    skipped = 0

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.startswith("## "):
            title = line[3:].strip()
            if title == "项目介绍":
                cur_cat, cur_group, last_item = None, None, None
                continue
            cur_cat = {"id": cid(title), "t": title, "g": []}
            cats.append(cur_cat)
            cur_group, last_item = None, None
            continue

        if cur_cat is None:
            continue

        if line.startswith("### "):
            title = line[3:].strip()
            if not full and title in EXCLUDE_GROUPS:
                cur_group = {"__skip__": True}
                last_item = None
                skipped += 1
                continue
            cur_group = {"id": cid(title), "t": title, "items": [], "notes": []}
            cur_cat["g"].append(cur_group)
            last_item = None
            continue

        if cur_group is None or cur_group.get("__skip__"):
            continue

        if line.startswith("#### "):
            cur_group["notes"].append("<strong>%s</strong>" % inline_md(line[5:].strip()))
            continue

        m = re.match(r"^(\s*)-\s+(.*)$", line)
        if m:
            item = parse_item(m.group(2))
            if not item:
                continue
            if len(m.group(1)) >= 2 and last_item is not None:
                last_item["c"].append(item)
            else:
                cur_group["items"].append(item)
                last_item = item
            continue

        if line.strip():
            note = inline_md(line.strip())
            if note:
                if last_item is not None:
                    last_item["no"].append(note)
                else:
                    cur_group["notes"].append(note)

    # 清掉空分组
    for cat in cats:
        cat["g"] = [g for g in cat["g"] if g.get("items")]
    cats = [c for c in cats if c["g"]]
    return cats, skipped


# ----------------------------------------------------------------------------
# 模板
# ----------------------------------------------------------------------------

BASE_CSS = r"""
:root{--bg:#f6f7f9;--surface:#fff;--surface-2:#fbfcfd;--border:#e4e7ec;--border-2:#eef0f3;
--text:#1c2027;--text-2:#4b5563;--muted:#7a8492;--accent:#2563eb;--accent-soft:#eef4ff;
--shadow:0 1px 2px rgba(16,24,40,.04),0 4px 14px rgba(16,24,40,.04);
--radius:12px;--sidebar:246px;--header:64px}
[data-theme="dark"]{--bg:#111418;--surface:#1a1f26;--surface-2:#1e242c;--border:#2b323c;--border-2:#252c35;
--text:#e9edf2;--text-2:#b6bfcb;--muted:#8b95a3;--accent:#6ea8ff;--accent-soft:#1b2a44;
--shadow:0 1px 2px rgba(0,0,0,.2),0 6px 18px rgba(0,0,0,.18)}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:132px}
body{margin:0;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"SF Pro SC","PingFang SC","Helvetica Neue","Microsoft YaHei",sans-serif;
font-size:14px;line-height:1.7;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
button,input{font:inherit}
::selection{background:#cfe0ff;color:#102a58}
"""

HEADER_CSS = r"""
.topbar{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--surface) 88%,transparent);
backdrop-filter:saturate(180%) blur(14px);border-bottom:1px solid var(--border)}
.topbar-in{max-width:1400px;margin:0 auto;height:var(--header);padding:0 28px;display:flex;align-items:center;gap:14px}
.brand{display:flex;align-items:center;gap:11px;min-width:0}
.logo{width:34px;height:34px;border-radius:9px;flex:none;display:grid;place-items:center;color:#fff;font-weight:800;
font-size:17px;background:linear-gradient(140deg,#3b82f6,#1e40af);box-shadow:0 3px 10px rgba(37,99,235,.25)}
.brand-t{font-size:16px;font-weight:700;letter-spacing:.2px;white-space:nowrap}
.brand-s{font-size:12px;color:var(--muted);white-space:nowrap}
.topbar-sp{flex:1}
.stat{font-size:12px;color:var(--muted);white-space:nowrap}
.stat b{color:var(--text);font-weight:650;font-variant-numeric:tabular-nums}
.icon-btn{width:34px;height:34px;border:1px solid var(--border);background:var(--surface);border-radius:9px;
display:grid;place-items:center;cursor:pointer;color:var(--text-2);transition:.15s}
.icon-btn:hover{border-color:var(--accent);color:var(--accent)}
.gh-btn{display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 12px;border:1px solid var(--border);
border-radius:9px;background:var(--surface);font-size:12.5px;font-weight:600;color:var(--text-2);transition:.15s}
.gh-btn:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-1px)}
.menu-btn{display:none}
.searchbar{position:sticky;top:var(--header);z-index:45;background:var(--bg);border-bottom:1px solid var(--border)}
.searchbar-in{max-width:1400px;margin:0 auto;padding:12px 28px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.search-box{position:relative;flex:1;min-width:220px;max-width:460px}
.search-box svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted)}
#q{width:100%;height:38px;padding:0 62px 0 36px;border:1px solid var(--border);border-radius:10px;
background:var(--surface);color:var(--text);outline:none;transition:.15s}
#q:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.kbd{position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:11px;color:var(--muted);
border:1px solid var(--border);border-radius:5px;padding:1px 6px;background:var(--surface-2)}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{height:30px;padding:0 12px;border:1px solid var(--border);background:var(--surface);border-radius:99px;
font-size:12.5px;color:var(--text-2);cursor:pointer;transition:.15s}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.result-count{font-size:12.5px;color:var(--muted);margin-left:auto;white-space:nowrap}
.result-count b{color:var(--accent);font-variant-numeric:tabular-nums}
"""

LAYOUT_CSS = r"""
.wrap{max-width:1400px;margin:0 auto;padding:26px 28px 80px;display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);gap:30px}
.sidebar{position:sticky;top:calc(var(--header) + 66px);align-self:start;max-height:calc(100vh - var(--header) - 92px);
overflow-y:auto;padding-right:6px}
.sidebar::-webkit-scrollbar{width:5px}.sidebar::-webkit-scrollbar-thumb{background:var(--border);border-radius:9px}
.side-title{font-size:11px;font-weight:700;letter-spacing:1.2px;color:var(--muted);text-transform:uppercase;margin:0 0 10px 10px}
.side-link{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:8px;color:var(--text-2);font-size:13px;transition:.12s}
.side-link:hover{background:var(--surface);color:var(--accent)}
.side-link.on{background:var(--accent-soft);color:var(--accent);font-weight:650}
.side-num{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;min-width:18px}
.side-link.on .side-num{color:var(--accent)}
.side-count{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.side-sep{height:1px;background:var(--border);margin:14px 10px}
.main{min-width:0}
.hero{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:26px 30px;margin-bottom:26px;
box-shadow:var(--shadow);position:relative;overflow:hidden}
.hero:before{content:"";position:absolute;inset:0 0 auto 0;height:3px;
background:linear-gradient(90deg,#2563eb,#60a5fa,#a78bfa,#f472b6)}
.hero h1{margin:0 0 8px;font-size:23px;letter-spacing:-.3px}
.hero p{margin:0;color:var(--text-2);font-size:13.5px;max-width:760px}
.hero-meta{display:flex;gap:22px;margin-top:18px;flex-wrap:wrap}
.hero-meta div{font-size:12px;color:var(--muted)}
.hero-meta b{display:block;font-size:19px;color:var(--text);font-weight:700;font-variant-numeric:tabular-nums;line-height:1.4}
.cat{margin-bottom:34px;scroll-margin-top:132px}
.cat-head{display:flex;align-items:baseline;gap:12px;padding-bottom:11px;border-bottom:1px solid var(--border);margin-bottom:18px}
.cat-no{font-size:12px;font-weight:800;color:var(--accent);letter-spacing:1px;font-variant-numeric:tabular-nums;flex:none}
.cat-head h2{margin:0;font-size:19px;letter-spacing:-.2px}
.cat-head>span:last-child{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap}
.grp{margin-bottom:22px;scroll-margin-top:132px}
.grp-head{display:flex;align-items:center;gap:9px;margin:0 0 11px}
.grp-head h3{margin:0;font-size:14px;font-weight:700}
.grp-head:after{content:"";height:1px;flex:1;background:var(--border-2)}
.grp-head em{font-style:normal;font-size:11px;color:var(--muted);order:3}
.note{background:var(--surface-2);border-left:3px solid var(--border);padding:8px 12px;border-radius:0 8px 8px 0;
font-size:12.5px;color:var(--text-2);margin:0 0 11px}
.note p{margin:0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:11px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:13px 15px;
box-shadow:var(--shadow);transition:.16s;min-width:0}
.card:hover{border-color:#b9cdf7;transform:translateY(-2px);box-shadow:0 6px 22px rgba(37,99,235,.09)}
.card-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.item-name{font-size:14px;font-weight:650;color:var(--text);display:inline-flex;align-items:center;gap:5px}
a.item-name:hover{color:var(--accent)}
.item-name svg{opacity:.45;flex:none}
.badge{font-size:10.5px;line-height:1.6;padding:0 6px;border-radius:5px;font-weight:650;white-space:nowrap}
.b-荐{background:#fff5e0;color:#b45309;border:1px solid #fde3ae}
.b-hot{background:#ffeceb;color:#c0392b;border:1px solid #fbd0cc}
.b-开源{background:#e8f7ee;color:#1a7f45;border:1px solid #c6ecd6}
.b-免费{background:#eaf2ff;color:#1d4ed8;border:1px solid #cfe0ff}
.b-付费{background:#f1f2f4;color:#5b6472;border:1px solid #e1e4e9}
[data-theme="dark"] .b-荐{background:#3a2c12;color:#f6c46a;border-color:#584425}
[data-theme="dark"] .b-hot{background:#3c1f1e;color:#f58f86;border-color:#5a2c29}
[data-theme="dark"] .b-开源{background:#143323;color:#7bd8a4;border-color:#205039}
[data-theme="dark"] .b-免费{background:#152744;color:#8eb8ff;border-color:#24406b}
[data-theme="dark"] .b-付费{background:#262c35;color:#aeb7c4;border-color:#343c47}
.card-desc{font-size:12.5px;color:var(--text-2);margin:7px 0 0;word-break:break-word}
.card-desc a,.item-note a{color:var(--accent);text-decoration:underline;text-underline-offset:2px;text-decoration-color:#b9cdf7}
.card-desc code,.article code{background:var(--surface-2);border:1px solid var(--border-2);border-radius:4px;padding:1px 5px;font-size:.9em}
.item-note{font-size:12px;color:var(--muted);margin-top:7px}
.item-note p{margin:0 0 3px}
.sub-items{margin:9px 0 0;padding:8px 0 0 12px;border-top:1px dashed var(--border);list-style:none}
.sub-items li{font-size:12.5px;color:var(--text-2);padding:2px 0}
.sub-items a{color:var(--accent)}
.docs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:11px}
.doc-card{display:block;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
padding:14px 16px;box-shadow:var(--shadow);transition:.16s}
.doc-card:hover{border-color:#b9cdf7;transform:translateY(-2px)}
.doc-card b{display:block;font-size:13.5px;font-weight:650;margin-bottom:4px}
.doc-card small{color:var(--muted);font-size:11.5px}
.empty{text-align:center;padding:70px 20px;color:var(--muted)}
.empty b{display:block;font-size:16px;color:var(--text-2);margin-bottom:6px}
footer{max-width:1400px;margin:0 auto;padding:0 28px 40px;color:var(--muted);font-size:12px;line-height:1.9}
footer .foot-in{border-top:1px solid var(--border);padding-top:18px}
footer a{color:var(--accent)}
.totop{position:fixed;right:24px;bottom:24px;width:38px;height:38px;border-radius:50%;border:1px solid var(--border);
background:var(--surface);color:var(--text-2);cursor:pointer;box-shadow:var(--shadow);display:none;place-items:center;z-index:60}
.totop.show{display:grid}
#clear{background:none;border:0;color:var(--accent);cursor:pointer;font-size:12.5px}
@media(max-width:960px){
.wrap{grid-template-columns:1fr;gap:0;padding:18px 16px 60px}
.sidebar{position:fixed;left:0;top:calc(var(--header) + 60px);bottom:0;width:270px;background:var(--surface);
border-right:1px solid var(--border);z-index:44;padding:18px 12px;transform:translateX(-102%);transition:.22s;max-height:none}
.sidebar.open{transform:none;box-shadow:12px 0 40px rgba(0,0,0,.14)}
.menu-btn{display:grid}
.topbar-in,.searchbar-in{padding-left:16px;padding-right:16px}
.brand-s,.stat{display:none}
.hero{padding:20px}
.cards{grid-template-columns:1fr}
footer{padding:0 16px 30px}
.result-count{margin-left:0}
}
"""

ARTICLE_CSS = r"""
.doc-wrap{max-width:960px;margin:0 auto;padding:34px 28px 90px;display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:36px}
.article{min-width:0;background:var(--surface);border:1px solid var(--border);border-radius:16px;
padding:34px 38px;box-shadow:var(--shadow)}
.crumb{font-size:12px;color:var(--muted);margin-bottom:18px}
.crumb a{color:var(--accent)}
.article h1{font-size:26px;margin:0 0 8px;letter-spacing:-.4px}
.article .meta{font-size:12px;color:var(--muted);padding-bottom:18px;border-bottom:1px solid var(--border);margin-bottom:22px}
.article h2{font-size:20px;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--border-2)}
.article h3{font-size:16px;margin:26px 0 10px}
.article h4{font-size:14px;margin:20px 0 8px}
.article p,.article li{color:var(--text-2);font-size:14px;line-height:1.85}
.article ul,.article ol{padding-left:22px}
.article li{margin:4px 0}
.article a{color:var(--accent);text-decoration:underline;text-underline-offset:2px;text-decoration-color:#c9daff}
.article blockquote{margin:14px 0;padding:10px 16px;background:var(--accent-soft);border-left:3px solid var(--accent);
border-radius:0 8px 8px 0;color:var(--text-2)}
.article pre{background:#0f172a;color:#e2e8f0;padding:16px 18px;border-radius:10px;overflow-x:auto;
font-size:12.5px;line-height:1.7}
.article pre code{background:none;border:0;padding:0;color:inherit;font-size:inherit}
.article img{max-width:100%;height:auto;border-radius:10px;border:1px solid var(--border);display:block;margin:14px auto}
.article figure{margin:16px 0;text-align:center}
.article hr{border:0;border-top:1px solid var(--border);margin:26px 0}
.table-wrap{overflow-x:auto;margin:14px 0}
.article table{border-collapse:collapse;width:100%;font-size:13px}
.article th,.article td{border:1px solid var(--border);padding:8px 11px;text-align:left}
.article th{background:var(--surface-2);font-weight:650}
.toc{position:sticky;top:calc(var(--header) + 80px);align-self:start;font-size:12.5px}
.toc b{display:block;font-size:11px;letter-spacing:1px;color:var(--muted);text-transform:uppercase;margin-bottom:10px}
.toc a{display:block;padding:4px 0 4px 10px;border-left:2px solid var(--border);color:var(--text-2);
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.toc a:hover{border-color:var(--accent);color:var(--accent)}
.toc a.lv3{padding-left:22px;font-size:12px;color:var(--muted)}
.doc-nav{display:flex;gap:12px;margin-top:36px;padding-top:20px;border-top:1px solid var(--border)}
.doc-nav a{flex:1;padding:11px 14px;background:var(--surface-2);border:1px solid var(--border);border-radius:10px;
font-size:12.5px;color:var(--text-2)}
.doc-nav a:hover{border-color:var(--accent);color:var(--accent)}
.doc-nav small{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}
@media(max-width:900px){.doc-wrap{grid-template-columns:1fr;padding:20px 14px 60px}.toc{display:none}.article{padding:22px 18px}}
"""

INDEX_JS = r"""
(function(){
const D=JSON.parse(document.getElementById('app-data').textContent);
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const strip=h=>String(h||'').replace(/<[^>]*>/g,'');
const extIcon='<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
let q='',tag='all';
const catCounts=D.cats.map(c=>c.g.reduce((n,g)=>n+g.items.length+g.items.reduce((m,i)=>m+i.c.length,0),0));
const total=catCounts.reduce((a,b)=>a+b,0);
function side(){
  let h='<div class="side-title">软件分类</div>';
  D.cats.forEach((c,i)=>{h+='<a class="side-link" data-cat="'+i+'" href="#cat-'+i+'"><span class="side-num">'+String(i+1).padStart(2,'0')+'</span><span>'+esc(c.t.replace(/^[一二三四五六七八九十]+、/,''))+'</span><span class="side-count">'+catCounts[i]+'</span></a>';});
  h+='<div class="side-sep"></div><div class="side-title">使用技巧 · 文档</div>';
  D.folders.forEach((f,i)=>{h+='<a class="side-link" href="#docs-'+i+'"><span class="side-num">+</span><span>'+esc(f.label)+'</span><span class="side-count">'+f.docs.length+'</span></a>';});
  $('#sidebar').innerHTML=h;
}
function badges(bs){return (bs||[]).map(b=>'<span class="badge b-'+b+'">'+esc(b)+'</span>').join('');}
function card(it){
  let h='<div class="card"><div class="card-top">';
  h+=it.u?'<a class="item-name" href="'+esc(it.u)+'" target="_blank" rel="noopener noreferrer">'+esc(it.n)+extIcon+'</a>'
         :'<span class="item-name">'+esc(it.n)+'</span>';
  h+=badges(it.b)+'</div>';
  if(it.d)h+='<div class="card-desc">'+it.d+'</div>';
  (it.no||[]).forEach(n=>{h+='<div class="item-note">'+n+'</div>';});
  if(it.c&&it.c.length){h+='<ul class="sub-items">';
    it.c.forEach(s=>{h+='<li>'+(s.u?'<a href="'+esc(s.u)+'" target="_blank" rel="noopener noreferrer">'+esc(s.n)+'</a>':'<span>'+esc(s.n)+'</span>')+(s.d?' — '+s.d:'')+badges(s.b)+'</li>';});
    h+='</ul>';}
  return h+'</div>';
}
function match(it,c,g){
  if(tag!=='all'&&!(it.b||[]).includes(tag))return false;
  if(!q)return true;
  const hay=(it.n+' '+strip(it.d)+' '+c.t+' '+g.t+' '+(it.c||[]).map(x=>x.n+' '+strip(x.d)).join(' ')).toLowerCase();
  return hay.includes(q);
}
function render(){
  let html='',shown=0;
  D.cats.forEach((c,ci)=>{
    let groups='',cn=0;
    c.g.forEach((g,gi)=>{
      const items=g.items.filter(it=>match(it,c,g));
      if(!items.length)return;
      const sub=items.reduce((m,i)=>m+(i.c||[]).length,0);
      cn+=items.length+sub;shown+=items.length+sub;
      groups+='<section class="grp" id="grp-'+ci+'-'+gi+'"><div class="grp-head"><h3>'+esc(g.t)+'</h3><em>'+items.length+'</em></div>';
      (g.notes||[]).forEach(n=>{if(!q)groups+='<div class="note">'+n+'</div>';});
      groups+='<div class="cards">'+items.map(card).join('')+'</div></section>';
    });
    if(!cn)return;
    html+='<section class="cat" id="cat-'+ci+'"><div class="cat-head"><span class="cat-no">'+String(ci+1).padStart(2,'0')+'</span><h2>'+esc(c.t.replace(/^[一二三四五六七八九十]+、/,''))+'</h2><span>'+cn+' 项</span></div>'+groups+'</section>';
  });
  D.folders.forEach((f,fi)=>{
    if(tag!=='all')return; // 标签只作用于软件条目，筛选时隐藏文档区
    let cards=f.docs.filter(d=>!q||(d.t+' '+f.label).toLowerCase().includes(q)).map(d=>
      '<a class="doc-card" href="pages/'+esc(d.s)+'.html"><b>'+esc(d.t)+'</b><small>'+esc(f.label)+' · 阅读文档 →</small></a>').join('');
    if(!cards)return;
    shown+=f.docs.filter(d=>!q||(d.t+' '+f.label).toLowerCase().includes(q)).length;
    html+='<section class="cat" id="docs-'+fi+'"><div class="cat-head"><span class="cat-no">DOC</span><h2>'+esc(f.label)+'</h2><span>'+f.docs.length+' 篇</span></div><div class="docs-grid">'+cards+'</div></section>';
  });
  $('#content').innerHTML=html||'<div class="empty"><b>没有找到匹配结果</b>换个关键词，或清除筛选条件</div>';
  $('#count').innerHTML=(q||tag!=='all')?'匹配 <b>'+shown+'</b> 项 <button id="clear">清除筛选</button>':'共收录 <b>'+total+'</b> 款软件 / 工具';
  const cl=$('#clear');if(cl)cl.onclick=()=>{q='';tag='all';$('#q').value='';document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x.dataset.tag==='all'));render();};
}
// 顶部 hero 的统计数字
$('#m-apps').textContent=total;
$('#m-cats').textContent=D.cats.length;
$('#m-grps').textContent=D.cats.reduce((n,c)=>n+c.g.length,0);
$('#m-docs').textContent=D.folders.reduce((n,f)=>n+f.docs.length,0);
side();render();
$('#q').addEventListener('input',e=>{q=e.target.value.trim().toLowerCase();render();});
document.querySelectorAll('.chip').forEach(ch=>ch.addEventListener('click',()=>{
  tag=ch.dataset.tag;document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===ch));render();}));
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#q').focus();}
  if(e.key==='Escape'&&document.activeElement===$('#q')){q='';$('#q').value='';$('#q').blur();render();}
  if(e.key==='/'&&document.activeElement!==$('#q')){e.preventDefault();$('#q').focus();}
});
$('#sidebar').addEventListener('click',e=>{const a=e.target.closest('.side-link');if(!a)return;
  if(q||tag!=='all'){q='';tag='all';$('#q').value='';document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x.dataset.tag==='all'));render();}
  if(window.innerWidth<=960)$('#sidebar').classList.remove('open');});
$('#menu').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
$('#theme').addEventListener('click',()=>{const d=document.documentElement.dataset.theme==='dark';
  document.documentElement.dataset.theme=d?'light':'dark';localStorage.setItem('ml-theme',d?'light':'dark');});
if(localStorage.getItem('ml-theme')==='dark'||(!localStorage.getItem('ml-theme')&&matchMedia('(prefers-color-scheme:dark)').matches))
  document.documentElement.dataset.theme='dark';
const totop=$('#totop');addEventListener('scroll',()=>totop.classList.toggle('show',scrollY>500));
totop.addEventListener('click',()=>scrollTo({top:0,behavior:'smooth'}));
})();
"""

INDEX_TMPL = r"""<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mac 清单 · Mac 软件与技巧导航</title>
<meta name="description" content="Mac 软件清单、Mac 使用技巧整理。按 8 大类收录数百款 macOS 应用与工具，支持即时搜索与标签筛选。">
<meta name="theme-color" content="#f6f7f9">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%232563eb'/%3E%3Ctext x='32' y='44' font-size='38' font-family='Arial' font-weight='bold' fill='white' text-anchor='middle'%3EM%3C/text%3E%3C/svg%3E">
<style>__CSS__</style>
</head>
<body>
<header class="topbar"><div class="topbar-in">
<button class="icon-btn menu-btn" id="menu" aria-label="目录"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M3 12h18M3 18h18"/></svg></button>
<div class="brand"><div class="logo">M</div><div><div class="brand-t">Mac 清单</div><div class="brand-s">Mac 软件清单 &amp; 使用技巧</div></div></div>
<div class="topbar-sp"></div>
<div class="stat">数据同步自 <b>qianguyihao/Mac-list</b></div>
<button class="icon-btn" id="theme" title="切换深浅色" aria-label="切换主题"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg></button>
<a class="gh-btn" href="__ORIGIN__" target="_blank" rel="noopener noreferrer"><svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .5A11.5 11.5 0 0 0 .5 12a11.5 11.5 0 0 0 7.86 10.92c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.12 3.05.74.81 1.18 1.84 1.18 3.1 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12 11.5 11.5 0 0 0 12 .5z"/></svg> 原仓库</a>
</div></header>
<div class="searchbar"><div class="searchbar-in">
<div class="search-box"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
<input id="q" type="search" placeholder="搜索软件、功能、分类…" autocomplete="off" aria-label="搜索"><span class="kbd">⌘ K</span></div>
<div class="chips"><button class="chip on" data-tag="all">全部</button><button class="chip" data-tag="荐">推荐</button><button class="chip" data-tag="开源">开源</button><button class="chip" data-tag="免费">免费</button><button class="chip" data-tag="付费">付费</button></div>
<div class="result-count" id="count"></div>
</div></div>
<div class="wrap">
<aside class="sidebar" id="sidebar"></aside>
<main class="main">
<div class="hero"><h1>把 Mac 用好，从这份清单开始</h1>
<p>按 8 个大类整理 macOS 常用软件、进阶工具、效率办公与开发利器，并收录配套的使用技巧文档。支持即时搜索、标签筛选，数据由原仓库 Markdown 自动构建。</p>
<div class="hero-meta"><div><b id="m-apps">0</b>软件工具</div><div><b id="m-cats">0</b>大类</div><div><b id="m-grps">0</b>细分场景</div><div><b id="m-docs">0</b>篇技巧文档</div></div></div>
<div id="content"></div>
</main></div>
<footer><div class="foot-in">内容源自 <a href="__ORIGIN__" target="_blank" rel="noopener noreferrer">qianguyihao/Mac-list</a>（作者：千古壹号），本站为非官方静态镜像，仅做 Markdown → 网页的格式转换。<br>原仓库未声明开源许可证；如需二次发布、商用或长期运营，请先联系原作者取得授权。软件链接与描述版权归各自所有者。</div></footer>
<button class="totop" id="totop" aria-label="回到顶部"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m18 15-6-6-6 6"/></svg></button>
<script id="app-data" type="application/json">__DATA__</script>
<script>__JS__</script>
</body></html>
"""

ARTICLE_TMPL = r"""<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · Mac 清单</title>
<meta name="theme-color" content="#f6f7f9">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%232563eb'/%3E%3Ctext x='32' y='44' font-size='38' font-family='Arial' font-weight='bold' fill='white' text-anchor='middle'%3EM%3C/text%3E%3C/svg%3E">
<style>__CSS__</style>
</head>
<body>
<header class="topbar"><div class="topbar-in">
<div class="brand"><div class="logo">M</div><div><div class="brand-t">Mac 清单</div><div class="brand-s">Mac 软件清单 &amp; 使用技巧</div></div></div>
<div class="topbar-sp"></div>
<button class="icon-btn" id="theme" title="切换深浅色" aria-label="切换主题"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg></button>
<a class="gh-btn" href="../index.html">← 返回清单</a>
</div></header>
<div class="doc-wrap">
<article class="article">
<div class="crumb"><a href="../index.html">首页</a> / __FOLDER__ / __TITLE__</div>
<h1>__TITLE__</h1><div class="meta">内容源自 qianguyihao/Mac-list · __SOURCE__</div>
<div class="md-body">__BODY__</div>
__NAV__
</article>
<aside class="toc"><b>本页目录</b>__TOC__</aside>
</div>
<footer><div class="foot-in">内容源自 <a href="__ORIGIN__" target="_blank" rel="noopener noreferrer">qianguyihao/Mac-list</a>（作者：千古壹号），本站为非官方静态镜像。原仓库未声明开源许可证。</div></footer>
<script>
(function(){const d=document.documentElement,t=localStorage.getItem('ml-theme');
if(t==='dark'||(!t&&matchMedia('(prefers-color-scheme:dark)').matches))d.dataset.theme='dark';
document.getElementById('theme').addEventListener('click',()=>{const x=d.dataset.theme==='dark';d.dataset.theme=x?'light':'dark';localStorage.setItem('ml-theme',x?'light':'dark');});})();
</script>
</body></html>
"""


# ----------------------------------------------------------------------------
# 构建
# ----------------------------------------------------------------------------

def ensure_source(refresh: bool) -> Path:
    src = ROOT / ".cache" / "Mac-list"
    if refresh and src.exists():
        shutil.rmtree(src)
    if not (src / "README.md").exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        print("[info] 正在拉取源仓库 …")
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(src)], check=True)
    return src


def clean_appledouble(root: Path) -> int:
    """外置卷（exFAT 等）写文件时会生成 ._* 伴生文件，体积虚高且不应提交到 GitHub。"""
    n = 0
    for p in root.rglob("._*"):
        if p.is_file():
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def collect_docs(src: Path, full: bool):
    folders = []
    for folder in FOLDER_ORDER:
        d = src / folder
        if not d.is_dir():
            continue
        docs = []
        for p in sorted(d.glob("*.md")):
            # exFAT 等外置卷会为每个文件生成 ._* AppleDouble 伴生文件，必须跳过
            if p.name.startswith("._") or p.name.startswith("~$"):
                continue
            if not full and p.name in EXCLUDE_DOCS:
                continue
            docs.append({
                "t": re.sub(r"^\d+[-－]\s*", "", p.stem).strip(),
                "s": "",
                "path": p,
                "rel": folder + "/" + p.name,
                "base": folder + "/",
            })
        if docs:
            folders.append({"label": FOLDER_LABELS[folder], "folder": folder, "docs": docs})
    idx = 0
    for f in folders:
        for doc in f["docs"]:
            idx += 1
            doc["s"] = "p%02d" % idx
    return folders


def toc_from_html(body: str) -> str:
    items = re.findall(r'<h([23]) id="([^"]+)">(.*?)</h\1>', body, re.S)
    if not items:
        return '<div style="color:var(--muted)">本文无小节标题</div>'
    out = []
    for level, anchor, text in items[:60]:
        plain = re.sub(r"<[^>]+>", "", text)
        out.append('<a class="%s" href="#%s">%s</a>' % ("lv3" if level == "3" else "", anchor, plain))
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description="Mac-list -> GitHub Pages")
    ap.add_argument("--source", default=None, help="源仓库目录（含 README.md 与分类文件夹）")
    ap.add_argument("--out", default=None, help="输出目录，默认 ./docs")
    ap.add_argument("--full", action="store_true", help="不做合规过滤，1:1 完整镜像")
    ap.add_argument("--refresh", action="store_true", help="强制重新拉取源仓库")
    args = ap.parse_args()

    src = Path(args.source).resolve() if args.source else (ROOT if (ROOT / "README.md").exists() and (ROOT / "01-新手指南").is_dir() else ensure_source(args.refresh))
    out = Path(args.out).resolve() if args.out else ROOT / "docs"
    if not (src / "README.md").exists():
        sys.exit("[error] 找不到 README.md：%s" % src)

    print("[info] 源目录: %s" % src)
    print("[info] 输出目录: %s" % out)
    cats, skipped = parse_readme((src / "README.md").read_text(encoding="utf-8"), args.full)
    folders = collect_docs(src, args.full)

    if not args.full:
        print("[info] 合规过滤: 跳过 %d 个小节及相关文档（--full 可关闭）" % skipped)

    data = {
        "cats": cats,
        "folders": [{"label": f["label"], "docs": [{"t": d["t"], "s": d["s"]} for d in f["docs"]]} for f in folders],
    }
    json_blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    out.mkdir(parents=True, exist_ok=True)
    pages = out / "pages"
    if pages.exists():
        shutil.rmtree(pages)
    pages.mkdir(parents=True)

    css_main = BASE_CSS + HEADER_CSS + LAYOUT_CSS
    index_html = (INDEX_TMPL
                  .replace("__CSS__", css_main)
                  .replace("__ORIGIN__", ORIGIN_REPO)
                  .replace("__DATA__", json_blob)
                  .replace("__JS__", INDEX_JS))
    (out / "index.html").write_text(index_html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    all_docs = [d for f in folders for d in f["docs"]]
    css_art = BASE_CSS + HEADER_CSS + ARTICLE_CSS
    for i, doc in enumerate(all_docs):
        text = doc["path"].read_text(encoding="utf-8", errors="replace")
        body = md_to_html(text, doc["base"])
        toc = toc_from_html(body)
        prev_d = all_docs[i - 1] if i > 0 else None
        next_d = all_docs[i + 1] if i + 1 < len(all_docs) else None
        nav = '<div class="doc-nav">'
        nav += ('<a href="%s.html"><small>← 上一篇</small>%s</a>' % (prev_d["s"], esc(prev_d["t"]))
                if prev_d else '<a href="../index.html"><small>← 返回</small>软件清单首页</a>')
        nav += ('<a href="%s.html" style="text-align:right"><small>下一篇 →</small>%s</a>' % (next_d["s"], esc(next_d["t"]))
                if next_d else '<a href="../index.html" style="text-align:right"><small>返回 →</small>软件清单首页</a>')
        nav += "</div>"
        folder_label = FOLDER_LABELS.get(doc["rel"].split("/")[0], "文档")
        page = (ARTICLE_TMPL
                .replace("__CSS__", css_art)
                .replace("__TITLE__", esc(doc["t"]))
                .replace("__FOLDER__", esc(folder_label))
                .replace("__SOURCE__", esc(doc["rel"]))
                .replace("__BODY__", body)
                .replace("__TOC__", toc)
                .replace("__NAV__", nav)
                .replace("__ORIGIN__", ORIGIN_REPO))
        (pages / (doc["s"] + ".html")).write_text(page, encoding="utf-8")

    removed = clean_appledouble(out)
    if removed:
        print("[info] 已清理 %d 个 ._* 伴生文件" % removed)

    n_items = sum(len(g["items"]) for c in cats for g in c["g"])
    n_sub = sum(len(i["c"]) for c in cats for g in c["g"] for i in g["items"])
    print("[done] %d 大类 / %d 小节 / %d 条目（含 %d 子项）/ %d 篇文章"
          % (len(cats), sum(len(c["g"]) for c in cats), n_items + n_sub, n_sub, len(all_docs)))
    print("[done] 首页: %s" % (out / "index.html"))


if __name__ == "__main__":
    main()
