import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MDNDoc:
    filename: str
    title: str | None
    source_url: str
    markdown: str


_FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)\n---\s*\n", re.MULTILINE)
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def ensure_git_repo(repo_dir: Path, repo_url: str, ref: str = "main", *, update: bool = True) -> None:
    repo_dir = repo_dir.resolve()
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    if not repo_dir.exists():
        subprocess.check_call(["git", "clone", "--depth", "1", "--branch", ref, repo_url, str(repo_dir)])
        return

    if not (repo_dir / ".git").exists():
        raise RuntimeError(f"MDN_REPO_DIR 不是 git 仓库目录：{repo_dir}")

    if not update:
        return

    subprocess.check_call(["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", ref])
    subprocess.check_call(["git", "-C", str(repo_dir), "checkout", ref])
    subprocess.check_call(["git", "-C", str(repo_dir), "reset", "--hard", f"origin/{ref}"])


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def iter_mdn_markdown_paths(
    repo_dir: Path,
    *,
    lang: str,
    include_paths: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_files: int = 0,
) -> list[Path]:
    """
    返回要导入的 .md 文件路径列表（按路径排序）。

    mdn/content (英文) 主要在：content/files/en-us/...
    mdn/translated-content (多语言) 主要在：files/zh-cn/...（以及其它语言）
    """
    repo_dir = repo_dir.resolve()
    include_paths = include_paths or []
    exclude_globs = exclude_globs or []

    # 兼容两类仓库的目录结构：
    # - mdn/content: content/files/<lang>/
    # - mdn/translated-content: files/<lang>/
    candidates = [
        repo_dir / "content" / "files" / lang.lower(),
        repo_dir / "files" / lang.lower(),
    ]
    base = next((p for p in candidates if p.exists()), None)
    if base is None:
        raise RuntimeError(f"未找到 MDN 文档目录（lang={lang}）：尝试过 {candidates}")

    roots: list[Path]
    if include_paths:
        roots = []
        missing: list[str] = []
        for rel in include_paths:
            rel = rel.strip().replace("\\", "/")
            if not rel:
                continue
            relp = (repo_dir / rel).resolve()
            if relp.exists():
                roots.append(relp)
                continue
            # 兼容：用户给了 mdn/content 的 content/files/... 但当前仓库是 translated-content 的 files/...
            # 或者反过来。这里做一次轻量“自动纠错”，让配置更不容易踩坑。
            alt_rel = None
            if rel.startswith("content/files/"):
                alt_rel = rel[len("content/") :]  # -> files/...
            elif rel.startswith("files/"):
                alt_rel = "content/" + rel  # -> content/files/...
            if alt_rel:
                altp = (repo_dir / alt_rel).resolve()
                if altp.exists():
                    roots.append(altp)
                    continue
            missing.append(rel)

        if not roots:
            hint = (
                "MDN_INCLUDE_PATHS 指定的路径均不存在。\n"
                f"- 当前仓库根目录：{repo_dir}\n"
                f"- 当前 lang：{lang}\n"
                f"- 你给的 include_paths：{include_paths}\n"
                "提示：\n"
                "- mdn/content 的路径通常形如：content/files/en-us/...\n"
                "- mdn/translated-content 的路径通常形如：files/zh-cn/...\n"
                "请把 MDN_INCLUDE_PATHS 改成匹配当前仓库结构的路径，或留空表示全量导入。"
            )
            raise RuntimeError(hint)
    else:
        roots = [base]

    out: list[Path] = []
    for root in roots:
        for p in root.rglob("*.md"):
            rel = p.relative_to(repo_dir).as_posix()
            if any(fnmatch.fnmatch(rel, g) for g in exclude_globs):
                continue
            out.append(p)

    out = sorted(set(out), key=lambda x: x.as_posix())
    if max_files and max_files > 0:
        out = out[: max_files]
    return out


def mdn_source_url(repo_dir: Path, file_path: Path, *, lang: str) -> str:
    repo_dir = repo_dir.resolve()
    rel = file_path.resolve().relative_to(repo_dir).as_posix()

    # mdn/content: content/files/en-us/web/javascript/.../index.md
    m = re.search(r"content/files/([^/]+)/(.+?)/index\.md$", rel, flags=re.IGNORECASE)
    if m:
        mdn_lang = m.group(1).lower()
        slug = m.group(2)
        return f"https://developer.mozilla.org/{mdn_lang}/docs/{slug}"

    # translated-content: files/zh-cn/web/javascript/.../index.md
    m = re.search(r"files/([^/]+)/(.+?)/index\.md$", rel, flags=re.IGNORECASE)
    if m:
        mdn_lang = m.group(1).lower()
        slug = m.group(2)
        return f"https://developer.mozilla.org/{mdn_lang}/docs/{slug}"

    # 非标准位置：尽量构造一个可追溯的 repo path URL（仍然可作为 source）
    safe_rel = rel.replace(" ", "%20")
    return f"mdn-repo://{safe_rel}"


def parse_mdn_markdown(raw: str) -> tuple[str, dict[str, str]]:
    """
    移除 frontmatter 并返回 (markdown_body, frontmatter_kv)。
    这里不引入额外依赖（如 python-frontmatter），用轻量解析足够支撑 title 等字段。
    """
    text = raw.replace("\r\n", "\n")
    meta: dict[str, str] = {}

    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text.strip(), meta

    fm = m.group(1)
    body = text[m.end() :].lstrip("\n")
    for line in fm.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        if k and v:
            meta[k] = v
    return body.strip(), meta


def extract_title(markdown: str, meta: dict[str, str] | None = None) -> str | None:
    meta = meta or {}
    for key in ("title", "slug", "short-title"):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    m = _H1_RE.search(markdown)
    if m:
        return m.group(1).strip()[:200]
    return None


def load_mdn_doc(repo_dir: Path, file_path: Path, *, lang: str) -> MDNDoc:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    body, meta = parse_mdn_markdown(raw)
    title = extract_title(body, meta)
    url = mdn_source_url(repo_dir, file_path, lang=lang)
    return MDNDoc(
        filename=file_path.name,
        title=title,
        source_url=url,
        markdown=body,
    )


def default_repo_dir_from_backend_cwd(repo_dir_setting: str) -> Path:
    """
    Settings 里默认给的是相对路径（相对 backend/ 启动目录）。
    为避免用户从仓库根目录运行脚本时路径错位，这里做一次显式规范化：
    - 若传入的是绝对路径：直接用
    - 否则：相对当前工作目录拼接
    """
    p = Path(repo_dir_setting)
    if p.is_absolute():
        return p
    return (Path(os.getcwd()) / p).resolve()

