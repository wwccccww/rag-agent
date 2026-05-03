/** 与后端 `app.kb.slugify_doc_type` / `normalize_doc_type` 规则一致（小写、1–32 位 [a-z0-9_-]） */

const DOC_TYPE_RE = /^[a-z0-9_-]{1,32}$/;

export const PRESET_DOC_TYPES = ["tutorial", "api", "requirements", "general"] as const;

export const PRESET_DOC_TYPE_SET = new Set<string>(PRESET_DOC_TYPES);

/** 与聊天页「配置检索文档类型」快捷一致，仅本地保存 */
export const USER_ID_KEY = "rag_user_id";
export const DOC_SHORTCUTS_KEY = (uid: string) => `rag_doc_type_shortcuts_${uid}`;
export const MAX_DOC_SHORTCUTS = 16;

export function loadDocShortcutsForUser(uid: string): string[] {
  try {
    const raw = localStorage.getItem(DOC_SHORTCUTS_KEY(uid));
    if (!raw) return [];
    const p = JSON.parse(raw) as unknown;
    if (!Array.isArray(p)) return [];
    const out: string[] = [];
    for (const x of p) {
      if (typeof x !== "string") continue;
      const s = slugDocType(x);
      if (s && !PRESET_DOC_TYPE_SET.has(s) && !out.includes(s)) out.push(s);
    }
    return out.slice(0, MAX_DOC_SHORTCUTS);
  } catch {
    return [];
  }
}

export function saveDocShortcutsForUser(uid: string, list: string[]) {
  try {
    const cleaned = list
      .map((x) => slugDocType(x))
      .filter((x): x is string => !!x && !PRESET_DOC_TYPE_SET.has(x))
      .filter((x, i, a) => a.indexOf(x) === i)
      .slice(0, MAX_DOC_SHORTCUTS);
    localStorage.setItem(DOC_SHORTCUTS_KEY(uid), JSON.stringify(cleaned));
  } catch {
    /* ignore */
  }
}

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
