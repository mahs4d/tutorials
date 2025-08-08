#!/usr/bin/env python3
"""Build a print-quality PDF book out of chapters/*.md.

Markdown is parsed with markdown-it-py (CommonMark + GFM tables), rendered to
LaTeX, and typeset with XeLaTeX. Code fences are syntax-highlighted with
Pygments. The visual design follows the interior conventions of O'Reilly
technical books; see tools/preamble.tex.

Usage:
    /usr/bin/python3 tools/build_pdf.py                 # full book
    /usr/bin/python3 tools/build_pdf.py --only 1,6,22   # subset, for iteration
    /usr/bin/python3 tools/build_pdf.py --tex-only      # stop after book.tex

Needs a Python with markdown_it and pygments importable (/usr/bin/python3 on
this machine) plus xelatex on PATH.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import LatexFormatter
from pygments.lexers import get_lexer_by_name
from pygments.style import Style
from pygments.token import (Comment, Error, Generic, Keyword, Literal, Name,
                            Number, Operator, Punctuation, String, Text, Token)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHAPTERS = ROOT / "chapters"
BUILD = ROOT / "build"
PREAMBLE = ROOT / "tools" / "preamble.tex"

DEFAULT_TITLE = "Core Java Preparation"
DEFAULT_SUBTITLE = "A Code-Review Interview Companion for Java 21+"
DEFAULT_AUTHOR = "Mahdi Sadeghi"


# --------------------------------------------------------------------------
# Syntax highlighting
# --------------------------------------------------------------------------
class BookStyle(Style):
    """A light, print-legible palette in the spirit of GitHub's light theme."""

    background_color = "#F6F7F8"
    styles = {
        Token:                  "#16191C",
        Comment:                "italic #5C6670",
        Comment.Preproc:        "noitalic #953800",
        Keyword:                "bold #A9203B",
        Keyword.Type:           "bold #A9203B",
        Keyword.Constant:       "bold #A9203B",
        Operator:               "#16191C",
        Operator.Word:          "bold #A9203B",
        Punctuation:            "#16191C",
        Name:                   "#16191C",
        Name.Builtin:           "#0A4E8C",
        Name.Builtin.Pseudo:    "italic #0A4E8C",
        Name.Class:             "#8A4B00",
        Name.Namespace:         "#8A4B00",
        Name.Exception:         "#8A4B00",
        Name.Function:          "#6A32A8",
        Name.Decorator:         "#8A4B00",
        Name.Attribute:         "#0A4E8C",
        Name.Tag:               "#12592B",
        Name.Constant:          "#0A4E8C",
        Name.Variable:          "#16191C",
        String:                 "#0B3B70",
        String.Escape:          "bold #0B3B70",
        String.Doc:             "italic #5C6670",
        Number:                 "#0A4E8C",
        Literal:                "#0A4E8C",
        Generic.Prompt:         "#5C6670",
        Generic.Output:         "#16191C",
        Generic.Emph:           "italic",
        Generic.Strong:         "bold",
        Error:                  "#A9203B",
    }


FORMATTER = LatexFormatter(style=BookStyle, commandprefix="PY")

LEXER_FOR = {
    "java": "java", "bash": "bash", "sh": "bash", "shell": "console",
    "console": "console", "xml": "xml", "kotlin": "kotlin", "groovy": "groovy",
    "properties": "properties", "toml": "toml", "json": "json",
    "yaml": "yaml", "gradle": "groovy",
}

_lexer_cache: dict[str, object] = {}


def lexer_for(lang: str):
    lang = (lang or "").strip().lower().split()[0] if lang.strip() else ""
    name = LEXER_FOR.get(lang)
    if not name:
        return None
    if name not in _lexer_cache:
        _lexer_cache[name] = get_lexer_by_name(name, stripnl=False, ensurenl=True)
    return _lexer_cache[name]


VERB_RE = re.compile(r"\\begin\{Verbatim\}.*?\n(.*)\\end\{Verbatim\}\s*\Z", re.S)


def highlight_body(src: str, lang: str) -> tuple[str, bool]:
    """Return (verbatim body, uses_pygments_commandchars)."""
    lexer = lexer_for(lang)
    if lexer is None:
        return src.rstrip("\n"), False
    out = highlight(src, lexer, FORMATTER)
    m = VERB_RE.search(out)
    body = m.group(1) if m else out
    return body.rstrip("\n"), True


# --------------------------------------------------------------------------
# Escaping
# --------------------------------------------------------------------------
TEXT_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#",
    "%": r"\%", "_": r"\_", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}", "<": r"\textless{}", ">": r"\textgreater{}",
    "|": r"\textbar{}",
}
TEXT_RE = re.compile("[" + re.escape("".join(TEXT_ESCAPES)) + "]")

# Emoji that none of the available fonts can render. These live inside Java
# string literals in the localization chapter, where the point being made is
# about UTF-16 code units -- so the equivalent \\u escapes are both valid Java
# and a faithful rendering of the example.
EMOJI_ESCAPES = {
    "\U0001F1F3\U0001F1F1": r"\uD83C\uDDF3\uD83C\uDDF1",
    "\U0001F468\u200D\U0001F469\u200D\U0001F467":
        r"\uD83D\uDC68\u200D\uD83D\uDC69\u200D\uD83D\uDC67",
}


def apply_emoji_escapes(s: str) -> str:
    for glyph, esc in EMOJI_ESCAPES.items():
        s = s.replace(glyph, esc)
    return s


def esc(s: str) -> str:
    return TEXT_RE.sub(lambda m: TEXT_ESCAPES[m.group()], s)


BREAK_AFTER = set(".,;:()[]{}/_-<>=+&|*!?@")


def esc_code(s: str) -> str:
    """Escape for \\texttt, inserting break opportunities in long identifiers.

    Breaks are allowed after punctuation and inside long camelCase names, so a
    span like `ArrayIndexOutOfBoundsException` can be split rather than running
    into the margin. Short names (`RuntimeException`) are left intact -- a break
    there reads as two separate words for no good reason.
    """
    out: list[str] = []
    n = len(s)
    camel_ok = n > 22
    for i, ch in enumerate(s):
        if ch == "'":
            out.append(r"\textquotesingle{}")
        elif ch == "`":
            out.append(r"\textasciigrave{}")
        elif ch == '"':
            out.append(r"\textquotedbl{}")
        elif ch == " ":
            out.append("~")           # fixed-width space, as in a listing
        else:
            out.append(TEXT_ESCAPES.get(ch, ch))
        if i + 1 < n:
            nxt = s[i + 1]
            if ch == " ":
                out.append(r"\allowbreak{}")
            elif ch in BREAK_AFTER and nxt not in BREAK_AFTER:
                out.append(r"\allowbreak{}")
            elif camel_ok and ch.islower() and nxt.isupper():
                out.append(r"\allowbreak{}")
    return "".join(out)


def slugify(text: str) -> str:
    """GitHub-flavoured anchor slug (keeps repeated hyphens, as GitHub does)."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def label_of(chapter_idx: int, slug: str) -> str:
    return f"h:{chapter_idx}:{slug}"


MD_FILE_RE = re.compile(r"^(\d{2})-[a-z0-9-]+\.md$")


# --------------------------------------------------------------------------
# Chapter model
# --------------------------------------------------------------------------
class Chapter:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.number = int(path.stem.split("-")[0])
        self.text = path.read_text(encoding="utf-8")
        m = re.search(r"^# +(.*)$", self.text, re.M)
        raw = m.group(1).strip() if m else path.stem
        self.title = re.sub(r"^\d+\.\s*", "", raw)


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------
class Renderer:
    def __init__(self, md: MarkdownIt, slugs: dict, chapters_by_number: dict,
                 stats: Counter):
        self.md = md
        self.slugs = slugs                  # slug -> [(chapter_number, label)]
        self.chapters_by_number = chapters_by_number
        self.stats = stats

    # -- inline ----------------------------------------------------------
    def inline(self, token, chapter: Chapter, *, plain: bool = False) -> str:
        out: list[str] = []
        link_stack: list[str] = []
        for t in token.children or []:
            ty = t.type
            if ty == "text":
                out.append(t.content if plain else esc(t.content))
            elif ty == "code_inline":
                content = t.content
                mdref = MD_FILE_RE.match(content.strip())
                if mdref:
                    # "see `13-concurrency-core.md`" -> a real cross-reference
                    n = int(mdref.group(1))
                    target = self.chapters_by_number.get(n)
                    if target:
                        self.stats["xref-chapter"] += 1
                        if plain:
                            out.append(f"Chapter {n}")
                        else:
                            out.append(
                                rf"\hyperref[chap:{n}]{{Chapter~{n}, "
                                rf"\emph{{{esc(target.title)}}}}}")
                        continue
                if plain:
                    out.append(content)
                else:
                    out.append(r"\code{" + esc_code(content) + "}")
            elif ty == "strong_open":
                out.append("" if plain else r"\textbf{")
            elif ty == "em_open":
                out.append("" if plain else r"\emph{")
            elif ty == "s_open":
                out.append("" if plain else r"\sout{")
            elif ty in ("strong_close", "em_close", "s_close"):
                out.append("" if plain else "}")
            elif ty == "link_open":
                href = t.attrGet("href") or ""
                link_stack.append(href)
                if plain:
                    continue
                if href.startswith("#"):
                    lbl = self.resolve_anchor(href[1:], chapter)
                    out.append(rf"\hyperref[{lbl}]{{" if lbl else "{")
                elif href.startswith(("http://", "https://")):
                    out.append(rf"\href{{{href}}}{{")
                else:
                    out.append("{")
            elif ty == "link_close":
                link_stack.pop() if link_stack else None
                out.append("" if plain else "}")
            elif ty == "softbreak":
                out.append(" " if plain else "\n")
            elif ty == "hardbreak":
                out.append(" " if plain else "\\\\\n")
            elif ty == "html_inline":
                self.stats["html_inline"] += 1
                out.append("" if plain else esc(self.strip_tags(t.content)))
            elif ty == "image":
                self.stats["image-skipped"] += 1
            else:
                self.stats[f"inline:{ty}"] += 1
                if t.content:
                    out.append(t.content if plain else esc(t.content))
        s = "".join(out)
        return re.sub(r"\s+", " ", s).strip() if plain else s

    @staticmethod
    def strip_tags(s: str) -> str:
        return re.sub(r"<[^>]*>", "", s)

    def resolve_anchor(self, slug: str, chapter: Chapter) -> str | None:
        entries = self.slugs.get(slug)
        if not entries:
            self.stats["anchor-unresolved"] += 1
            return None
        for num, lbl in entries:
            if num == chapter.number:
                return lbl
        return entries[0][1]

    # -- blocks ----------------------------------------------------------
    def render(self, chapter: Chapter) -> str:
        tokens = self.md.parse(chapter.text)
        out: list[str] = []
        i = 0
        n = len(tokens)
        list_depth = 0
        skip_until_level = None        # dropping a "Table of Contents" section
        details_depth = 0

        while i < n:
            t = tokens[i]
            ty = t.type

            # ---- dropping the per-chapter Table of Contents -------------
            if skip_until_level is not None:
                if ty == "heading_open" and int(t.tag[1]) <= skip_until_level:
                    skip_until_level = None
                else:
                    i += 1
                    continue

            if ty == "heading_open":
                level = int(t.tag[1])
                inline_tok = tokens[i + 1]
                plain = self.inline(inline_tok, chapter, plain=True)
                if level == 2 and plain.lower() == "table of contents":
                    self.stats["toc-section-dropped"] += 1
                    skip_until_level = 2
                    i += 3
                    continue
                body = self.inline(inline_tok, chapter)
                slug = slugify(plain)
                lbl = label_of(chapter.number, slug)
                if level == 1:
                    # The heading carries its own number ("12. Memory
                    # Management"); the chapter opener prints it separately.
                    body = re.sub(r"^\s*\d+\.\s*", "", body)
                    out.append(rf"\setcounter{{chapter}}{{{chapter.number - 1}}}")
                    out.append(rf"\chapter{{{body}}}")
                    out.append(rf"\label{{chap:{chapter.number}}}")
                    out.append(rf"\label{{{lbl}}}")
                else:
                    cmd = {2: "section", 3: "subsection", 4: "subsubsection",
                           5: "subsubsection", 6: "subsubsection"}[level]
                    short = plain.replace("\\", "").strip()
                    out.append(rf"\{cmd}[{esc(short)}]{{{body}}}")
                    out.append(rf"\label{{{lbl}}}")
                i += 3
                continue

            if ty == "paragraph_open":
                body = self.inline(tokens[i + 1], chapter)
                if list_depth:
                    out.append(body)
                    out.append("")
                else:
                    out.append(body)
                    out.append("")
                i += 3
                continue

            if ty == "fence" or ty == "code_block":
                out.append(self.code_block(t))
                i += 1
                continue

            if ty == "bullet_list_open":
                list_depth += 1
                out.append(r"\begin{itemize}")
                i += 1
                continue
            if ty == "bullet_list_close":
                list_depth -= 1
                out.append(r"\end{itemize}")
                out.append("")
                i += 1
                continue
            if ty == "ordered_list_open":
                list_depth += 1
                start = t.attrGet("start")
                if start:
                    out.append(rf"\begin{{enumerate}}[start={start}]")
                else:
                    out.append(r"\begin{enumerate}")
                i += 1
                continue
            if ty == "ordered_list_close":
                list_depth -= 1
                out.append(r"\end{enumerate}")
                out.append("")
                i += 1
                continue
            if ty == "list_item_open":
                out.append(r"\item ")
                i += 1
                continue
            if ty == "list_item_close":
                i += 1
                continue

            if ty == "blockquote_open":
                kind, i2 = self.admonition_kind(tokens, i)
                out.append(rf"\begin{{{kind}}}")
                self.stats[f"admonition:{kind}"] += 1
                i = i2
                continue
            if ty == "blockquote_close":
                out.append(rf"\end{{{self.open_admon.pop()}}}")
                out.append("")
                i += 1
                continue

            if ty == "table_open":
                block, i = self.table(tokens, i, chapter, inside_list=bool(list_depth))
                out.append(block)
                continue

            if ty == "hr":
                # In these sources `---` only ever precedes a heading, where the
                # heading itself is the visual break.
                nxt = tokens[i + 1] if i + 1 < n else None
                if not (nxt and nxt.type == "heading_open"):
                    out.append(r"\ornament")
                else:
                    self.stats["hr-dropped"] += 1
                i += 1
                continue

            if ty == "html_block":
                frag = t.content.strip()
                m = re.match(r"<details>\s*<summary>(.*?)</summary>", frag, re.S)
                if m:
                    title = re.sub(r"^show\s+(the\s+)?", "",
                                   self.strip_tags(m.group(1)).strip(),
                                   flags=re.I)
                    title = title[:1].upper() + title[1:] if title else "Details"
                    out.append(rf"\begin{{sidebar}}{{{esc(title)}}}")
                    details_depth += 1
                    self.stats["sidebar"] += 1
                elif frag.startswith("</details>"):
                    if details_depth:
                        out.append(r"\end{sidebar}")
                        details_depth -= 1
                else:
                    self.stats["html_block-dropped"] += 1
                i += 1
                continue

            if ty == "inline":
                out.append(self.inline(t, chapter))
                i += 1
                continue

            self.stats[f"block:{ty}"] += 1
            i += 1

        while details_depth:
            out.append(r"\end{sidebar}")
            details_depth -= 1
        return "\n".join(out) + "\n"

    open_admon: list[str] = []

    def admonition_kind(self, tokens, i) -> tuple[str, int]:
        """Pick a note/tip/warning box from a leading **Note**-style marker."""
        kind = "notebox"
        j = i + 1
        if j < len(tokens) and tokens[j].type == "paragraph_open":
            inline_tok = tokens[j + 1]
            lead = self.inline(inline_tok, None, plain=True)[:24].lower()
            if lead.startswith(("warning", "caution", "danger")):
                kind = "warnbox"
            elif lead.startswith(("tip", "hint", "best practice")):
                kind = "tipbox"
        self.open_admon.append(kind)
        return kind, i + 1

    def code_block(self, token) -> str:
        lang = (token.info or "").strip()
        src = apply_emoji_escapes(token.content)
        body, highlighted = highlight_body(src, lang)
        self.stats[f"code:{lang or 'plain'}"] += 1
        opts = ["commandchars=\\\\\\{\\}"] if highlighted else []
        opt = f"[{','.join(opts)}]" if opts else ""
        return (r"\begin{codebox}" "\n"
                rf"\begin{{Verbatim}}{opt}" "\n"
                f"{body}\n"
                r"\end{Verbatim}" "\n"
                r"\end{codebox}" "\n")

    # -- tables ----------------------------------------------------------
    def table(self, tokens, i, chapter, inside_list: bool):
        aligns: list[str] = []
        rows: list[list[str]] = []
        header: list[str] = []
        plain_rows: list[list[str]] = []
        in_header = False
        cur: list[str] = []
        cur_plain: list[str] = []
        n = len(tokens)
        while i < n:
            t = tokens[i]
            if t.type == "table_close":
                i += 1
                break
            if t.type == "thead_open":
                in_header = True
            elif t.type == "thead_close":
                in_header = False
            elif t.type == "tr_open":
                cur, cur_plain = [], []
            elif t.type == "tr_close":
                if in_header:
                    header = cur
                else:
                    rows.append(cur)
                plain_rows.append(cur_plain)
            elif t.type in ("th_open", "td_open"):
                style = t.attrGet("style") or ""
                if in_header:
                    if "center" in style:
                        aligns.append("C")
                    elif "right" in style:
                        aligns.append("R")
                    else:
                        aligns.append("L")
                inline_tok = tokens[i + 1]
                cell = self.inline(inline_tok, chapter) if inline_tok.type == "inline" else ""
                cell_plain = self.inline(inline_tok, chapter, plain=True) if inline_tok.type == "inline" else ""
                cur.append(cell)
                cur_plain.append(cell_plain)
                i += 3
                continue
            i += 1

        ncols = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
        if ncols == 0:
            return "", i
        aligns = (aligns + ["L"] * ncols)[:ncols]

        # Column weights from the typical content width, so a "Description"
        # column is not squeezed to the width of a "Type" column.
        widths = []
        for c in range(ncols):
            lens = [len(r[c]) for r in plain_rows if c < len(r)]
            lens.sort()
            typical = lens[int(len(lens) * 0.8)] if lens else 1
            widths.append(max(4, min(typical, 90)))
        total = sum(widths)
        weights = [max(0.45, ncols * w / total) for w in widths]
        scale = ncols / sum(weights)
        weights = [w * scale for w in weights]
        colspec = "".join(f"{aligns[c]}{{{weights[c]:.3f}}}" for c in range(ncols))

        widest = max((sum(len(c) for c in r) for r in plain_rows), default=0)
        font = r"\tablefontsmall" if widest > 150 else r"\tablefont"

        def row(cells: list[str], head=False) -> str:
            cells = cells + [""] * (ncols - len(cells))
            if head:
                cells = [rf"\thd{{{c}}}" for c in cells]
            return " & ".join(cells) + r" \\"

        env = "tabularx" if inside_list else "xltabular"
        lines = [r"{" + font,
                 rf"\begin{{{env}}}{{\linewidth}}{{@{{}}{colspec}@{{}}}}",
                 r"\toprule"]
        if header:
            lines.append(r"\rowcolor{tablehdr}")
            lines.append(row(header, head=True))
            lines.append(r"\midrule")
            if env == "xltabular":
                lines.append(r"\endhead")
        for r_ in rows:
            lines.append(row(r_))
        lines += [r"\bottomrule", rf"\end{{{env}}}", r"}", ""]
        self.stats["table"] += 1
        return "\n".join(lines), i


# --------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------
PREFACE = r"""
\chapter*{Preface}
\addcontentsline{toc}{chapter}{Preface}
\markboth{Preface}{Preface}

Each topic in this book is a self-contained chapter. Every chapter explains its
subtopics in plain language with runnable Java examples, and ends with a
\emph{Common Code-Review Interview Pitfalls} section that collects the mistakes
reviewers actually catch.

The material is written for someone who already knows Java but wants to review
it sharply --- the kind of review you do before a code-review-style interview.
Examples target Java~21 and later, with notes where older or newer releases
differ.

\section{Conventions Used in This Book}

The following typographic conventions are used:

\begin{description}[leftmargin=0pt, style=nextline, itemsep=5pt]
  \item[\emph{Italic}] Indicates emphasis and new terms where they are first
    defined.
  \item[\code{Constant width}] Used for program listings, as well as within
    paragraphs to refer to program elements such as variable and method names,
    data types, statements, keywords, class names, and command-line tools.
\end{description}

Code listings are set on a tinted background and syntax-highlighted. Where a
single line of code or shell output is too long for the measure, it wraps and
the continuation is marked with a $\hookrightarrow$ symbol in the left margin.

\begin{notebox}
This element signifies a general note.
\end{notebox}

\begin{tipbox}
This element signifies a tip or suggestion.
\end{tipbox}

\begin{warnbox}
This element indicates a warning or a caution.
\end{warnbox}

Sections marked off by rules and a small heading --- like \textsc{the review}
blocks in the final chapter --- hold worked answers. Try the exercise before
reading them.
"""


def colophon(title: str, author: str, chapters: list[Chapter], build_date: str) -> str:
    words = sum(len(c.text.split()) for c in chapters)
    return "\n".join([
        r"\thispagestyle{empty}",
        r"\vspace*{\fill}",
        r"{\raggedright\fontsize{9}{12.5}\selectfont\color{headgray}",
        rf"\textbf{{{esc(title)}}}\par\vspace{{3pt}}",
        rf"by {esc(author)}.\par\vspace{{9pt}}",
        rf"Typeset from the Markdown sources in \code{{chapters/}} on {esc(build_date)}: "
        rf"{len(chapters)} chapters, roughly {words:,} words. "
        r"Rebuild with \code{python3 tools/build\_pdf.py}.\par\vspace{9pt}",
        r"Body text is set in Erewhon, headings in Source Sans Pro, and code in "
        r"Source Code Pro. Code listings are highlighted with Pygments and the "
        r"book is typeset with XeLaTeX.\par\vspace{9pt}",
        r"Examples target Java 21 and later. Code in this book is intended for "
        r"study and may be used freely.\par}",
        r"\clearpage",
    ])


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def build_tex(chapters: list[Chapter], args) -> str:
    md = MarkdownIt("gfm-like", {"typographer": True, "linkify": False})
    md.enable(["replacements", "smartquotes"])

    # Pass 1: collect every heading slug from the token stream (so `#` lines
    # inside code fences are not mistaken for headings) to resolve in-book
    # anchor links later.
    slugs: dict[str, list[tuple[int, str]]] = {}
    for ch in chapters:
        toks = md.parse(ch.text)
        for j, tok in enumerate(toks):
            if tok.type == "heading_open":
                plain = re.sub(r"[*`_]", "", toks[j + 1].content).strip()
                plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)
                slug = slugify(plain)
                slugs.setdefault(slug, []).append(
                    (ch.number, label_of(ch.number, slug)))

    chapters_by_number = {c.number: c for c in chapters}
    stats: Counter = Counter()
    renderer = Renderer(md, slugs, chapters_by_number, stats)

    body_parts = [renderer.render(ch) for ch in chapters]

    preamble = PREAMBLE.read_text(encoding="utf-8")
    pyg_defs = FORMATTER.get_style_defs()
    build_date = args.date or dt.date.today().strftime("%d %B %Y")

    doc = [preamble, "", "% --- Pygments style definitions ---", pyg_defs, ""]
    doc.append("\n".join([
        r"\usepackage[unicode,bookmarksnumbered,bookmarksdepth=2,"
        r"colorlinks=true,linkcolor=linkblue,urlcolor=linkblue,"
        r"citecolor=linkblue,pdfborder={0 0 0}]{hyperref}",
        rf"\hypersetup{{pdftitle={{{args.title}}},pdfauthor={{{args.author}}},"
        rf"pdfsubject={{{args.subtitle}}},pdfcreator={{XeLaTeX}}}}",
        r"\begin{document}",
        r"\frontmatter",
        rf"\booktitleblock{{{esc(args.title)}}}{{{esc(args.subtitle)}}}"
        rf"{{{esc(args.author)}}}",
        colophon(args.title, args.author, chapters, build_date),
        r"\pdfbookmark[0]{Contents}{toc}",
        r"\tableofcontents",
        r"\cleardoublepage",
        PREFACE,
        r"\mainmatter",
    ]))
    doc.extend(body_parts)
    doc.append(r"\end{document}")
    tex = "\n".join(doc)

    print("--- conversion stats ---")
    for k, v in sorted(stats.items()):
        print(f"  {v:6d}  {k}")
    return tex


def run_xelatex(passes: int) -> pathlib.Path:
    for p in range(1, passes + 1):
        print(f"--- xelatex pass {p}/{passes} ---", flush=True)
        proc = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error",
             "-file-line-error", "book.tex"],
            cwd=BUILD, capture_output=True, text=True)
        if proc.returncode != 0:
            log = (BUILD / "book.log")
            tail = log.read_text(errors="replace").splitlines()[-60:] if log.exists() else []
            print(proc.stdout[-3000:])
            print("\n".join(tail))
            raise SystemExit(f"xelatex failed on pass {p}")
    return BUILD / "book.pdf"


def report_log() -> None:
    log = BUILD / "book.log"
    if not log.exists():
        return
    text = log.read_text(errors="replace")
    missing = Counter(re.findall(r"Missing character: There is no (.+?) in font", text))
    overfull = len(re.findall(r"Overfull \\hbox", text))
    underfull = len(re.findall(r"Underfull \\hbox", text))
    undef = Counter(re.findall(r"Reference `([^']+)' on page", text))
    print("--- typesetting report ---")
    print(f"  overfull hboxes : {overfull}")
    print(f"  underfull hboxes: {underfull}")
    if missing:
        print("  missing glyphs  :")
        for k, v in missing.most_common(20):
            print(f"      {v:4d}  {k}")
    else:
        print("  missing glyphs  : none")
    if undef:
        print(f"  undefined refs  : {len(undef)}")
    m = re.search(r"Output written on book\.pdf \((\d+) pages, (\d+) bytes\)", text)
    if m:
        print(f"  pages           : {m.group(1)}")
        print(f"  size            : {int(m.group(2))/1_048_576:.1f} MiB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--subtitle", default=DEFAULT_SUBTITLE)
    ap.add_argument("--author", default=DEFAULT_AUTHOR)
    ap.add_argument("--only", default="", help="comma-separated chapter numbers")
    ap.add_argument("--tex-only", action="store_true")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--date", default="")
    ap.add_argument("--out", default="java-prep-book.pdf")
    args = ap.parse_args()

    paths = sorted(CHAPTERS.glob("*.md"))
    chapters = [Chapter(p) for p in paths]
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        chapters = [c for c in chapters if c.number in wanted]
    if not chapters:
        print("no chapters found", file=sys.stderr)
        return 1
    print(f"building {len(chapters)} chapter(s)")

    BUILD.mkdir(exist_ok=True)
    tex = build_tex(chapters, args)
    (BUILD / "book.tex").write_text(tex, encoding="utf-8")
    print(f"wrote build/book.tex ({len(tex)/1024:.0f} KiB)")
    if args.tex_only:
        return 0

    run_xelatex(args.passes)
    report_log()
    out = ROOT / args.out
    shutil.copy(BUILD / "book.pdf", out)
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
