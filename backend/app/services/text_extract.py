import io
import logging
import re
from pathlib import Path

import httpx
from pypdf import PdfReader


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            if t.strip():
                parts.append(f"\n--- Page {i + 1} ---\n{t}")
        return "\n".join(parts).strip()

    if suffix == ".docx":
        try:
            from docx import Document as DocxDocument  # lazy import
            doc = DocxDocument(io.BytesIO(data))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            # 同时提取表格内容
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        paragraphs.append(" | ".join(cells))
            result = "\n\n".join(paragraphs)
            logging.info("[Extract] docx → %d chars, %d paragraphs", len(result), len(paragraphs))
            return result
        except ImportError as e:
            raise RuntimeError("python-docx 未安装，请运行 pip install python-docx") from e

    if suffix == ".xlsx":
        try:
            import openpyxl  # lazy import
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            parts: list[str] = []
            for sheet in wb.worksheets:
                parts.append(f"## Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        parts.append(" | ".join(cells))
            result = "\n".join(parts)
            logging.info("[Extract] xlsx → %d chars, %d sheets", len(result), len(wb.worksheets))
            return result
        except ImportError as e:
            raise RuntimeError("openpyxl 未安装，请运行 pip install openpyxl") from e

    # 默认按 UTF-8 文本处理（.txt / .md / .csv 等）
    return data.decode("utf-8", errors="replace")


def fetch_url(url: str, timeout: float = 15.0) -> tuple[str, str]:
    """抓取网页内容，返回 (纯文本, 页面标题)。依赖 beautifulsoup4 + lxml。"""
    try:
        from bs4 import BeautifulSoup  # lazy import，未安装时给出友好报错
    except ImportError as e:
        raise RuntimeError("beautifulsoup4 未安装，请运行 pip install beautifulsoup4 lxml") from e

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # 移除无用标签
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""

    # 提取正文：优先 <article>/<main>，否则 <body>
    body = soup.find("article") or soup.find("main") or soup.body
    if body is None:
        body = soup

    lines = [line.strip() for line in body.get_text(separator="\n").splitlines() if line.strip()]
    text = "\n".join(lines)
    logging.info("[URL] fetched %s → %d chars (title: %s)", url, len(text), title)
    return text, title


_MD_HEADING = re.compile(r"^#{1,6}\s+\S")
_CLOSING_FENCE_RE = re.compile(r"^`{3,}\s*$")


def _is_closing_fence_line(line: str) -> bool:
    """围栏结束行：整行（去首尾空白）仅由至少 3 个 ` 与可选空白组成。"""
    return bool(_CLOSING_FENCE_RE.match(line.strip()))


def _iter_text_and_fence_spans(section: str) -> list[tuple[str, str]]:
    """将一节正文拆成交替的 prose 与 fenced code（含首尾 ``` 行），围栏内原文不改。"""
    lines = section.replace("\r\n", "\n").split("\n")
    out: list[tuple[str, str]] = []
    text_buf: list[str] = []
    i = 0
    n = len(lines)

    def flush_text() -> None:
        if not text_buf:
            return
        block = "\n".join(text_buf)
        text_buf.clear()
        if block.strip():
            out.append(("text", block))

    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            flush_text()
            fence_lines = [line]
            i += 1
            while i < n:
                fence_lines.append(lines[i])
                if len(fence_lines) > 1 and _is_closing_fence_line(lines[i]):
                    i += 1
                    break
                i += 1
            joined = "\n".join(fence_lines)
            if joined.strip():
                out.append(("fence", joined))
            continue
        text_buf.append(line)
        i += 1
    flush_text()
    return out


def _section_to_mixed_units(section: str) -> list[tuple[str, str]]:
    """text 单元按空行拆成段落；fence 单元整块保留。"""
    units: list[tuple[str, str]] = []
    for kind, block in _iter_text_and_fence_spans(section):
        if kind == "text":
            for p in block.split("\n\n"):
                ps = p.strip()
                if ps:
                    units.append(("text", ps))
        elif block.strip():
            units.append(("fence", block))
    return units


def _looks_like_markdown(text: str, filename: str | None) -> bool:
    fn = (filename or "").lower()
    if fn.endswith(".md"):
        return True
    t = text.lstrip()
    if t.startswith("#"):
        return True
    return "\n## " in text or "\n### " in text


def _split_oversized_paragraph(p: str, max_chars: int, overlap: int) -> list[str]:
    """长段按句号/换行优先切，避免硬截断把语义切碎。"""
    if len(p) <= max_chars:
        return [p] if p.strip() else []
    out: list[str] = []
    start = 0
    n = len(p)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            ws = max(start, end - 140)
            window = p[ws:end]
            cut = -1
            for sep in ("。\n", "。", "！\n", "！", "？\n", "？", ". ", ".\n", "\n\n", "\n", "；", "; "):
                idx = window.rfind(sep)
                if idx != -1:
                    cut = ws + idx + len(sep)
                    break
            if cut > start:
                end = cut
        piece = p[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return out


def _split_oversized_fence(p: str, max_chars: int, _overlap: int) -> list[str]:
    """超长围栏块仅在换行处切分，避免在代码/XML 行内硬截断。

    子块之间**不使用**与正文相同的字符 _overlap：回退 overlap 容易落在行中，产生半截标签。
    """
    if len(p) <= max_chars:
        return [p] if p.strip() else []
    out: list[str] = []
    start = 0
    n = len(p)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            ws = max(start, end - 400)
            window = p[ws:end]
            cut = window.rfind("\n")
            if cut != -1:
                end = ws + cut + 1
            else:
                segment = p[start:end]
                cut2 = segment.rfind("\n")
                if cut2 != -1:
                    end = start + cut2 + 1
        piece = p[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = end
    return out


def _pack_paragraphs(paragraphs: list[str], max_chars: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_chars:
                buf = p
            else:
                for piece in _split_oversized_paragraph(p, max_chars, overlap):
                    chunks.append(piece)
                buf = ""
        while len(buf) > max_chars:
            chunks.append(buf[:max_chars])
            buf = buf[max_chars - overlap :]
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def _pack_mixed_units(units: list[tuple[str, str]], max_chars: int, overlap: int) -> list[str]:
    """在 text 单元上沿用段落合并与长段软切；fence 单元整块输出，超长时仅按换行切。"""
    chunks: list[str] = []
    buf = ""
    for kind, p in units:
        if kind == "fence":
            if buf and len(buf) + len(p) + 2 <= max_chars:
                buf = f"{buf}\n\n{p}"
                while len(buf) > max_chars:
                    chunks.append(buf[:max_chars])
                    buf = buf[max_chars - overlap :]
                continue
            if buf:
                chunks.append(buf)
                buf = ""
            if len(p) <= max_chars:
                chunks.append(p)
            else:
                chunks.extend(_split_oversized_fence(p, max_chars, overlap))
            continue
        # text
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_chars:
                buf = p
            else:
                for piece in _split_oversized_paragraph(p, max_chars, overlap):
                    chunks.append(piece)
                buf = ""
        while len(buf) > max_chars:
            chunks.append(buf[:max_chars])
            buf = buf[max_chars - overlap :]
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def _markdown_sections(text: str) -> list[str]:
    """按 Markdown 标题行分节，每节保留标题行在段首，便于向量携带主题。"""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    lines = text.split("\n")
    sections: list[str] = []
    buf: list[str] = []
    for line in lines:
        if _MD_HEADING.match(line.strip()) and buf:
            sec = "\n".join(buf).strip()
            if sec:
                sections.append(sec)
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sec = "\n".join(buf).strip()
        if sec:
            sections.append(sec)
    return sections if sections else [text]


def chunk_text(
    text: str,
    max_chars: int,
    overlap: int,
    *,
    filename: str | None = None,
    markdown_by_heading: bool = True,
    markdown_fence_aware: bool = True,
) -> list[tuple[str, dict]]:
    """
    将全文切块。Markdown（.md 或含 ## 标题）在 markdown_by_heading 为 True 时先按标题分节；
    markdown_fence_aware 且判定为 Markdown 时，节内识别 ``` 围栏，围栏内不按句号/短窗切分，
    超长围栏仅在换行处切；否则节内仍按空行段落合并后切块。
    """
    text = text.strip()
    if not text:
        return []

    use_md = markdown_by_heading and _looks_like_markdown(text, filename)
    if use_md:
        raw_sections = _markdown_sections(text)
    else:
        raw_sections = [text]

    chunks: list[str] = []
    for sec in raw_sections:
        if use_md and markdown_fence_aware:
            units = _section_to_mixed_units(sec)
            if units:
                chunks.extend(_pack_mixed_units(units, max_chars, overlap))
            elif sec.strip():
                chunks.extend(_pack_paragraphs([sec.strip()], max_chars, overlap))
        else:
            paragraphs = [p.strip() for p in sec.split("\n\n") if p.strip()]
            if not paragraphs:
                paragraphs = [sec] if sec.strip() else []
            chunks.extend(_pack_paragraphs(paragraphs, max_chars, overlap))

    out: list[tuple[str, dict]] = []
    for idx, c in enumerate(chunks):
        page = None
        if "--- Page " in c:
            try:
                line = c.split("\n", 1)[0]
                if line.startswith("--- Page ") and "---" in line:
                    num = line.replace("--- Page ", "").split(" ", 1)[0]
                    page = int(num)
            except (ValueError, IndexError):
                page = None
        heading = None
        for ln in c.split("\n")[:3]:
            s = ln.strip()
            if _MD_HEADING.match(s):
                heading = s.lstrip("#").strip()[:120]
                break
        meta: dict = {"chunk_index": idx, "page": page}
        if heading:
            meta["section_heading"] = heading
        out.append((c.strip(), meta))
    return out
