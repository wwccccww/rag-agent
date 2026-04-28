import io
import logging
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


def chunk_text(
    text: str,
    max_chars: int,
    overlap: int,
) -> list[tuple[str, dict]]:
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
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
                for i in range(0, len(p), max_chars - overlap):
                    chunks.append(p[i : i + max_chars])
                buf = ""
        while len(buf) > max_chars:
            chunks.append(buf[:max_chars])
            buf = buf[max_chars - overlap :]
    if buf:
        chunks.append(buf)
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
        out.append((c.strip(), {"chunk_index": idx, "page": page}))
    return out
