/** 与后端 `app.kb.slugify_doc_type` / `normalize_doc_type` 规则一致（小写、1–32 位 [a-z0-9_-]） */

const DOC_TYPE_RE = /^[a-z0-9_-]{1,32}$/;

export const PRESET_DOC_TYPES = ["tutorial", "api", "requirements", "general"] as const;

export function slugDocType(raw: string): string | null {
  let s = raw.trim().toLowerCase();
  if (!s) return null;
  s = s.replace(/\s+/g, "-");
  s = s.replace(/[^a-z0-9_-]+/g, "-");
  s = s.replace(/-+/g, "-").replace(/^[-_]+|[-_]+$/g, "");
  if (!s) return null;
  s = s.slice(0, 32).replace(/^[-_]+|[-_]+$/g, "");
  if (!s || !DOC_TYPE_RE.test(s)) return null;
  return s;
}

export function normalizeDocTypeOrThrow(raw: string): string {
  const t = slugDocType(raw);
  if (!t) {
    throw new Error(
      "非法 doc_type：需为 1–32 位小写字母、数字、下划线、连字符（空格会转为连字符）"
    );
  }
  return t;
}
