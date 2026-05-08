"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { PRESET_DOC_TYPES, slugDocType, USER_ID_KEY, loadDocShortcutsForUser } from "@/lib/kb";

type Doc = {
  id: string;
  title: string | null;
  source: string | null;
  kb_collection: string;
  doc_type: string;
  chunk_count: number;
  created_at: string;
};
type Chunk = {
  id: string;
  chunk_index: number;
  content: string;
  meta: Record<string, unknown>;
  is_index_chunk: boolean;
  parent_chunk_id: string | null;
};

/** 与检索侧一致：来自分块时的标题面包屑（父级 / 子级），存于 meta.section_heading */
function chunkSectionHeading(meta: Record<string, unknown>): string | null {
  const h = meta.section_heading;
  return typeof h === "string" && h.trim() ? h.trim() : null;
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Doc | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [kbFilter, setKbFilter] = useState("");
  const [docTypeFilter, setDocTypeFilter] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [batchKb, setBatchKb] = useState("");
  const [batchDocType, setBatchDocType] = useState("");
  const [batchSaving, setBatchSaving] = useState(false);
  const [customDocFilterDraft, setCustomDocFilterDraft] = useState("");
  const [filterTypeModalOpen, setFilterTypeModalOpen] = useState(false);
  const [batchTypeModalOpen, setBatchTypeModalOpen] = useState(false);
  /** 与聊天页「配置检索文档类型」自定义快捷同源（localStorage），便于跨页一致 */
  const [chatShortcutDocTypes, setChatShortcutDocTypes] = useState<string[]>([]);
  /** 当前库/分区内出现过的类型（与列表筛选无关），避免选中 api 后芯片只剩 api */
  const [catalogDocTypes, setCatalogDocTypes] = useState<string[]>([]);

  useEffect(() => {
    try {
      setKbFilter(localStorage.getItem("rag_kb_collection") ?? "");
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!filterTypeModalOpen && !batchTypeModalOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setFilterTypeModalOpen(false);
        setBatchTypeModalOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filterTypeModalOpen, batchTypeModalOpen]);

  useEffect(() => {
    if (!filterTypeModalOpen && !batchTypeModalOpen) return;
    try {
      const uid = localStorage.getItem(USER_ID_KEY) || "demo";
      setChatShortcutDocTypes(loadDocShortcutsForUser(uid));
    } catch {
      setChatShortcutDocTypes([]);
    }
  }, [filterTypeModalOpen, batchTypeModalOpen]);

  useEffect(() => {
    if (!filterTypeModalOpen && !batchTypeModalOpen) return;
    let cancelled = false;
    void (async () => {
      try {
        const uid = localStorage.getItem(USER_ID_KEY) || "demo";
        let url = `/api/documents/catalog/doc-types?user_id=${encodeURIComponent(uid)}`;
        if (kbFilter.trim()) {
          url += `&kb_collection=${encodeURIComponent(kbFilter.trim())}`;
        }
        const r = await fetch(url, { cache: "no-store" });
        if (!r.ok || cancelled) return;
        const j = (await r.json()) as { doc_types?: string[] };
        const list = Array.isArray(j.doc_types) ? j.doc_types : [];
        if (!cancelled) setCatalogDocTypes(list);
      } catch {
        if (!cancelled) setCatalogDocTypes([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filterTypeModalOpen, batchTypeModalOpen, kbFilter]);

  const docTypeOptions = useMemo(() => {
    const s = new Set<string>([...PRESET_DOC_TYPES]);
    for (const d of docs) s.add(d.doc_type);
    for (const t of catalogDocTypes) {
      const sl = slugDocType(t);
      if (sl) s.add(sl);
    }
    for (const t of chatShortcutDocTypes) {
      const sl = slugDocType(t);
      if (sl) s.add(sl);
    }
    return Array.from(s).sort();
  }, [docs, catalogDocTypes, chatShortcutDocTypes]);

  const docTypeFilterChips = useMemo(() => {
    const s = new Set<string>(docTypeOptions);
    const cur = docTypeFilter.trim().toLowerCase();
    if (cur) s.add(cur);
    return Array.from(s).sort();
  }, [docTypeOptions, docTypeFilter]);

  const applyCustomDocFilter = () => {
    const t = slugDocType(customDocFilterDraft);
    if (!t) {
      window.alert("无法解析为合法类型 slug（小写字母、数字、下划线、连字符）");
      return;
    }
    setDocTypeFilter(t);
    setCustomDocFilterDraft("");
  };

  const pickDocTypeFilter = (slug: string) => {
    setDocTypeFilter((prev) => (prev.trim().toLowerCase() === slug ? "" : slug));
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      let path = "/api/documents";
      const p = new URLSearchParams();
      try {
        p.set("user_id", localStorage.getItem(USER_ID_KEY) || "demo");
      } catch {
        p.set("user_id", "demo");
      }
      if (kbFilter.trim()) p.set("kb_collection", kbFilter.trim());
      if (docTypeFilter) p.set("doc_type", docTypeFilter);
      if (p.toString()) path += `?${p.toString()}`;
      const r = await fetch(path, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json().catch(() => []);
      setDocs(Array.isArray(data) ? data : []);
    } catch {
      // backend not ready, silently ignore
    } finally {
      setLoading(false);
    }
  }, [kbFilter, docTypeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const [chunkView, setChunkView] = useState<"parent" | "index">("parent");

  const openDoc = async (doc: Doc, view?: "parent" | "index") => {
    setSelected(doc);
    setChunks([]);
    setChunksLoading(true);
    const v = view ?? chunkView;
    try {
      const uid = localStorage.getItem(USER_ID_KEY) || "demo";
      const r = await fetch(
        `/api/documents/${doc.id}/chunks?view=${v}&user_id=${encodeURIComponent(uid)}`,
      );
      if (!r.ok) return;
      const data = await r.json().catch(() => []);
      setChunks(Array.isArray(data) ? data : []);
    } catch {
      // backend not ready
    } finally {
      setChunksLoading(false);
    }
  };

  const switchChunkView = async (v: "parent" | "index") => {
    setChunkView(v);
    if (selected) await openDoc(selected, v);
  };

  const deleteDoc = async (doc: Doc) => {
    if (!confirm(`确认删除「${doc.title ?? doc.source}」及其所有向量片段？`)) return;
    setDeleting(doc.id);
    const uid = localStorage.getItem(USER_ID_KEY) || "demo";
    await fetch(`/api/documents/${doc.id}?user_id=${encodeURIComponent(uid)}`, { method: "DELETE" });
    if (selected?.id === doc.id) { setSelected(null); setChunks([]); }
    setSelectedIds((prev) => {
      const n = new Set(prev);
      n.delete(doc.id);
      return n;
    });
    await load();
    setDeleting(null);
  };

  const toggleSelect = (id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const n = new Set(prev);
      if (checked) n.add(id);
      else n.delete(id);
      return n;
    });
  };

  const applyBatchMeta = async () => {
    if (selectedIds.size === 0) return;
    const idsSnapshot = new Set(selectedIds);
    const kb = batchKb.trim();
    const dt = batchDocType.trim();
    if (!kb && !dt) {
      window.alert("请填写目标分区（kb_collection）或选择文档类型");
      return;
    }
    const body: { document_ids: string[]; kb_collection?: string; doc_type?: string } = {
      document_ids: Array.from(idsSnapshot),
    };
    if (kb) body.kb_collection = kb;
    if (dt) body.doc_type = dt;
    setBatchSaving(true);
    try {
      const uid = localStorage.getItem(USER_ID_KEY) || "demo";
      const r = await fetch(
        `/api/documents/batch?user_id=${encodeURIComponent(uid)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const text = await r.text();
      if (!r.ok) {
        window.alert(text.slice(0, 500) || `请求失败 (${r.status})`);
        return;
      }
      if (selected && idsSnapshot.has(selected.id)) {
        setSelected(null);
        setChunks([]);
      }
      setSelectedIds(new Set());
      await load();
    } catch {
      window.alert("网络错误");
    } finally {
      setBatchSaving(false);
    }
  };

  return (
    <div className="app-shell">
      {/* left: doc list */}
      <aside className="sidebar" style={{ width: 312 }}>
        <div className="sidebar-header">
          <h1>知识库文档</h1>
          <p>{docs.length} 个文档</p>
          <div className="field-label" style={{ marginTop: 8 }}>筛选</div>
          <input
            className="userid-input"
            style={{ width: "100%", marginTop: 4 }}
            placeholder="kb_collection"
            value={kbFilter}
            onChange={(e) => {
              const v = e.target.value;
              setKbFilter(v);
              try {
                localStorage.setItem("rag_kb_collection", v);
              } catch {}
            }}
            aria-label="分区筛选"
          />
          <div className="documents-filter-block">
            <div className="field-label" style={{ marginTop: 2 }}>按类型筛选</div>
            <button
              type="button"
              className="btn type-modal-trigger"
              style={{ width: "100%", marginTop: 4 }}
              onClick={() => setFilterTypeModalOpen(true)}
            >
              {!docTypeFilter.trim()
                ? "全部类型 · 点击在窗口中选择"
                : `当前：${docTypeFilter}`}
            </button>
          </div>
          {selectedIds.size > 0 && (
            <div
              style={{
                marginTop: 10,
                padding: 10,
                border: "1px solid var(--border)",
                borderRadius: 8,
                background: "var(--panel)",
              }}
            >
              <div className="field-label" style={{ marginBottom: 6 }}>
                已选 {selectedIds.size} 项 · 批量改分区 / 类型（无需重新入库）
              </div>
              <input
                className="userid-input"
                style={{ width: "100%", marginBottom: 6 }}
                placeholder="目标 kb_collection（可空，与类型二选一）"
                value={batchKb}
                onChange={(e) => setBatchKb(e.target.value)}
                aria-label="批量目标分区"
              />
              <div className="field-label" style={{ marginBottom: 4 }}>目标类型</div>
              <button
                type="button"
                className="btn type-modal-trigger"
                style={{ width: "100%", marginBottom: 8 }}
                onClick={() => setBatchTypeModalOpen(true)}
              >
                {!batchDocType.trim()
                  ? "不改类型 · 点击在窗口中选择"
                  : `当前目标：${batchDocType}`}
              </button>
              <button
                type="button"
                className="btn"
                style={{ width: "100%", justifyContent: "center" }}
                disabled={batchSaving}
                onClick={() => void applyBatchMeta()}
              >
                {batchSaving ? "提交中…" : "批量应用"}
              </button>
              <button
                type="button"
                className="btn"
                style={{ width: "100%", justifyContent: "center", marginTop: 6, opacity: 0.85 }}
                onClick={() => setSelectedIds(new Set())}
              >
                清除勾选
              </button>
            </div>
          )}
        </div>
        <div className="sidebar-body">
          {loading && <div className="field-label" style={{ padding: "12px 10px" }}>加载中…</div>}
          {!loading && docs.length === 0 && (
            <div className="field-label" style={{ padding: "12px 10px" }}>暂无文档，请先入库</div>
          )}
          {docs.map((doc) => (
            <div
              key={doc.id}
              className={`sidebar-session${selected?.id === doc.id ? " active" : ""}`}
              style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}
              onClick={() => openDoc(doc)}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, width: "100%" }}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(doc.id)}
                  onChange={(e) => toggleSelect(doc.id, e.target.checked)}
                  onClick={(e) => e.stopPropagation()}
                  aria-label="勾选以批量修改分区或类型"
                />
                <span style={{ fontSize: 14 }}>📄</span>
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
                  {doc.title ?? doc.source ?? "无标题"}
                </span>
              </div>
              <div style={{ display: "flex", gap: 8, paddingLeft: 20, flexWrap: "wrap" }}>
                <span className="badge green" style={{ fontSize: 10 }}>{doc.kb_collection}</span>
                <span className="badge" style={{ fontSize: 10 }}>{doc.doc_type}</span>
                <span className="badge" style={{ fontSize: 10 }}>{doc.chunk_count} 个片段</span>
                <button
                  className="btn danger"
                  style={{ padding: "1px 8px", fontSize: 10, border: "none", background: "transparent" }}
                  disabled={deleting === doc.id}
                  onClick={(e) => { e.stopPropagation(); deleteDoc(doc); }}
                >
                  {deleting === doc.id ? "删除中…" : "删除"}
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          <a href="/ingest" className="btn" style={{ width: "100%", justifyContent: "center", marginBottom: 6 }}>📄 继续入库</a>
          <a href="/" className="btn" style={{ width: "100%", justifyContent: "center" }}>← 返回对话</a>
        </div>
      </aside>

      {/* right: chunk viewer */}
      <div className="main" style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <div className="topbar">
          <span className="topbar-title">
            {selected ? (selected.title ?? selected.source ?? selected.id.slice(0, 8)) : "选择左侧文档查看分块"}
          </span>
          {selected && <span className="badge blue">{selected.chunk_count} chunks</span>}
        </div>

        <div className="chat-area" style={{ gap: 12 }}>
          {!selected && (
            <div className="empty-state">
              <div className="empty-state-icon">🔍</div>
              <h3>文档分块预览</h3>
              <p>点击左侧文档，查看该文档被切分成的所有向量片段（chunks）内容与元数据。Markdown 按标题分节时，每条片段上方会显示标题面包屑（与对话里「节：…」同源，来自 <code>section_heading</code>）。</p>
            </div>
          )}

          {selected && chunksLoading && (
            <div className="field-label" style={{ padding: "20px 0" }}>加载中…</div>
          )}

          {selected && !chunksLoading && (
            <>
              {/* 父块/子块视图切换 */}
              <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                <button
                  onClick={() => void switchChunkView("parent")}
                  style={{
                    padding: "4px 14px",
                    borderRadius: 20,
                    border: "1px solid var(--border)",
                    background: chunkView === "parent" ? "var(--accent)" : "var(--panel)",
                    color: chunkView === "parent" ? "#fff" : "var(--text)",
                    cursor: "pointer",
                    fontSize: 12,
                    fontWeight: chunkView === "parent" ? 600 : 400,
                  }}
                  title="父块：完整语义段落，LLM 读取此内容作答"
                >
                  父块（LLM 读取）
                </button>
                <button
                  onClick={() => void switchChunkView("index")}
                  style={{
                    padding: "4px 14px",
                    borderRadius: 20,
                    border: "1px solid var(--border)",
                    background: chunkView === "index" ? "var(--accent)" : "var(--panel)",
                    color: chunkView === "index" ? "#fff" : "var(--text)",
                    cursor: "pointer",
                    fontSize: 12,
                    fontWeight: chunkView === "index" ? 600 : 400,
                  }}
                  title="检索子块：小粒度片段，用于向量/文本检索；命中后展开为对应父块"
                >
                  检索子块（向量检索）
                </button>
              </div>
              <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 8 }}>
                {chunkView === "parent"
                  ? "父块：每个完整语义段落（LLM 实际读取的内容）。旧格式文档不区分父子，直接显示所有切块。"
                  : "检索子块：粒度更细的片段，用于向量/文本检索；命中时自动展开为父块内容喂给 LLM。"}
              </div>
            </>
          )}

          {selected && !chunksLoading && chunks.map((c) => {
            const crumb = chunkSectionHeading(c.meta);
            const isParentChunk = !c.is_index_chunk;
            return (
            <div key={c.id} style={{
              border: `1px solid ${isParentChunk ? "var(--accent)" : "var(--border)"}`,
              borderRadius: 10,
              background: "var(--panel)",
              padding: 14,
              opacity: isParentChunk ? 1 : 0.9,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                <span className="badge blue">#{c.chunk_index}</span>
                {isParentChunk && (
                  <span className="badge" style={{ background: "var(--accent)", color: "#fff", fontSize: 10 }}>
                    父块
                  </span>
                )}
                {!isParentChunk && c.parent_chunk_id && (
                  <span className="badge" style={{ fontSize: 10, color: "var(--text-dim)" }}>
                    检索子块
                  </span>
                )}
                {c.meta.page != null && (
                  <span className="badge">第 {String(c.meta.page)} 页</span>
                )}
                <span className="badge" style={{ fontSize: 10, color: "var(--text-dim)" }}>
                  {c.content.length} 字符
                </span>
              </div>
              {crumb && (
                <div className="source-card-section" style={{ marginBottom: 10 }}>
                  节：{crumb}
                </div>
              )}
              <pre style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 12.5,
                lineHeight: 1.7,
                color: "var(--text)",
                margin: 0,
                fontFamily: "inherit",
                borderLeft: `2px solid ${isParentChunk ? "var(--accent)" : "var(--border)"}`,
                paddingLeft: 12,
              }}>
                {c.content}
              </pre>
            </div>
            );
          })}
        </div>
      </div>

      {filterTypeModalOpen && (
        <div
          className="type-modal-root"
          role="dialog"
          aria-modal="true"
          aria-labelledby="documents-filter-type-modal-title"
        >
          <div className="type-modal-backdrop" onClick={() => setFilterTypeModalOpen(false)} />
          <div className="type-modal-sheet">
            <header className="type-modal-header">
              <h2 id="documents-filter-type-modal-title" className="type-modal-title">
                按类型筛选文档
              </h2>
              <button
                type="button"
                className="type-modal-close"
                onClick={() => setFilterTypeModalOpen(false)}
                aria-label="关闭"
              >
                ×
              </button>
            </header>
            <div className="type-modal-body">
              <div className="field-label" style={{ marginBottom: 6 }}>单选类型</div>
              <div className="documents-type-chip-row" role="listbox" aria-label="文档类型筛选">
                <button
                  type="button"
                  className={`documents-type-chip${!docTypeFilter.trim() ? " active" : ""}`}
                  onClick={() => setDocTypeFilter("")}
                >
                  全部
                </button>
                {docTypeFilterChips.map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`documents-type-chip${docTypeFilter.trim().toLowerCase() === t ? " active" : ""}`}
                    onClick={() => pickDocTypeFilter(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="field-label" style={{ marginTop: 14, marginBottom: 6 }}>其它 slug</div>
              <div className="doc-type-add-row">
                <input
                  className="userid-input"
                  placeholder="输入后应用，如 internal-wiki"
                  value={customDocFilterDraft}
                  onChange={(e) => setCustomDocFilterDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      applyCustomDocFilter();
                    }
                  }}
                  aria-label="自定义文档类型筛选"
                />
                <button type="button" onClick={() => applyCustomDocFilter()}>
                  应用
                </button>
              </div>
            </div>
            <footer className="type-modal-footer">
              <button type="button" className="btn" onClick={() => setFilterTypeModalOpen(false)}>
                完成
              </button>
            </footer>
          </div>
        </div>
      )}

      {batchTypeModalOpen && (
        <div
          className="type-modal-root"
          role="dialog"
          aria-modal="true"
          aria-labelledby="documents-batch-type-modal-title"
        >
          <div className="type-modal-backdrop" onClick={() => setBatchTypeModalOpen(false)} />
          <div className="type-modal-sheet">
            <header className="type-modal-header">
              <h2 id="documents-batch-type-modal-title" className="type-modal-title">
                批量修改目标类型
              </h2>
              <button
                type="button"
                className="type-modal-close"
                onClick={() => setBatchTypeModalOpen(false)}
                aria-label="关闭"
              >
                ×
              </button>
            </header>
            <div className="type-modal-body">
              <div className="documents-type-chip-row">
                <button
                  type="button"
                  className={`documents-type-chip${!batchDocType.trim() ? " active" : ""}`}
                  onClick={() => setBatchDocType("")}
                >
                  不改类型
                </button>
                {docTypeOptions.map((t) => (
                  <button
                    key={`modal-batch-${t}`}
                    type="button"
                    className={`documents-type-chip${batchDocType.trim().toLowerCase() === t ? " active" : ""}`}
                    onClick={() => setBatchDocType((prev) => (prev.trim().toLowerCase() === t ? "" : t))}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="field-label" style={{ marginTop: 14, marginBottom: 6 }}>或手动输入 slug</div>
              <input
                className="userid-input"
                style={{ width: "100%" }}
                placeholder="目标 doc_type"
                value={batchDocType}
                onChange={(e) => setBatchDocType(e.target.value)}
                aria-label="批量目标文档类型"
              />
            </div>
            <footer className="type-modal-footer">
              <button type="button" className="btn" onClick={() => setBatchTypeModalOpen(false)}>
                完成
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
