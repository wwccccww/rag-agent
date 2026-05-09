import argparse
import logging
import sys
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.config import settings
from app.database import SessionLocal
from app.services.mdn_import import (
    default_repo_dir_from_backend_cwd,
    ensure_git_repo,
    iter_mdn_markdown_paths,
    load_mdn_doc,
)
from app.services.ollama import OllamaClient
from app.services.rag import ingest_bytes


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="批量导入 MDN Web Docs（方案A：GitHub Markdown 源）")
    parser.add_argument("--repo-url", default=settings.mdn_repo_url)
    parser.add_argument("--repo-dir", default=settings.mdn_repo_dir)
    parser.add_argument("--repo-ref", default=settings.mdn_repo_ref)
    parser.add_argument("--lang", default=settings.mdn_lang)
    parser.add_argument("--include-paths", default=settings.mdn_include_paths)
    parser.add_argument("--exclude-globs", default=settings.mdn_exclude_globs)
    parser.add_argument("--max-files", type=int, default=settings.mdn_max_files)
    parser.add_argument("--kb-collection", default=settings.default_kb_collection)
    parser.add_argument("--doc-type", default="mdn")
    parser.add_argument("--no-update", action="store_true", help="不拉取更新（只使用本地 repo-dir）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要导入的文件数，不落库")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")

    repo_dir = default_repo_dir_from_backend_cwd(args.repo_dir)
    t0 = time.perf_counter()
    ensure_git_repo(repo_dir, args.repo_url, args.repo_ref, update=not args.no_update)

    include_paths = _split_csv(args.include_paths)
    exclude_globs = _split_csv(args.exclude_globs)
    paths = iter_mdn_markdown_paths(
        repo_dir,
        lang=str(args.lang),
        include_paths=include_paths,
        exclude_globs=exclude_globs,
        max_files=int(args.max_files or 0),
    )
    logging.info("[MDN] repo=%s ref=%s lang=%s files=%d", repo_dir, args.repo_ref, args.lang, len(paths))
    if args.dry_run:
        return 0

    db = SessionLocal()
    client = OllamaClient()
    created_docs = 0
    total_chunks = 0
    try:
        for i, p in enumerate(paths, start=1):
            doc = load_mdn_doc(repo_dir, p, lang=str(args.lang))
            # ingest_bytes 会做 sha256 去重：同内容再次导入会返回 (existing_id, 0)
            doc_id, n = ingest_bytes(
                db,
                client,
                filename=doc.filename,
                data=doc.markdown.encode("utf-8"),
                title=doc.title,
                source=doc.source_url,
                kb_collection=str(args.kb_collection),
                doc_type=str(args.doc_type),
            )
            if n > 0:
                created_docs += 1
                total_chunks += n
            if i % 50 == 0:
                logging.info("[MDN] progress %d/%d (new_docs=%d, new_chunks=%d)", i, len(paths), created_docs, total_chunks)
        logging.info(
            "[MDN] done. scanned=%d new_docs=%d new_chunks=%d elapsed=%.1fs",
            len(paths),
            created_docs,
            total_chunks,
            time.perf_counter() - t0,
        )
        return 0
    finally:
        client.close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

