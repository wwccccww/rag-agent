"use client";

import { useCallback, useEffect, useState } from "react";

type Doc = {
  id: string;
  title: string | null;
  source: string | null;
  kb_collection: string;
  doc_type: string;
  chunk_count: number;
  created_at: string;
};
type Chunk = { id: string; chunk_index: number; content: string; meta: Record<string, unknown> };

export default function DocumentsPage() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Doc | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [kbFilter, setKbFilter] = useState("");
  const [docTypeFilter, setDocTypeFilter] = useState("");

  useEffect(() => {
    try {
      setKbFilter(localStorage.getItem("rag_kb_collection") ?? "");
    } catch {
      /* ignore */
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      let path = "/api/documents";
      const p = new URLSearchParams();
      if (kbFilter.trim()) p.set("kb_collection", kbFilter.trim());
      if (docTypeFilter) p.set("doc_type", docTypeFilter);
      if ([...p.keys()].length) path += `?${p.toString()}`;
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

  const openDoc = async (doc: Doc) => {
    setSelected(doc);
    setChunks([]);
    setChunksLoading(true);
    try {
      const r = await fetch(`/api/documents/${doc.id}/chunks`);
      if (!r.ok) return;
      const data = await r.json().catch(() => []);
      setChunks(Array.isArray(data) ? data : []);
    } catch {
      // backend not ready
    } finally {
      setChunksLoading(false);
    }
  };

  const deleteDoc = async (doc: Doc) => {
    if (!confirm(`确认删除「${doc.title ?? doc.source}」及其所有向量片段？`)) return;
    setDeleting(doc.id);
    await fetch(`/api/documents/${doc.id}`, { method: "DELETE" });
    if (selected?.id === doc.id) { setSelected(null); setChunks([]); }
    await load();
    setDeleting(null);
  };

  return (
    <div className="app-shell">
      {/* left: doc list */}
      <aside className="sidebar" style={{ width: 300 }}>
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
          <select
            className="userid-input"
            style={{ width: "100%", marginTop: 6 }}
            value={docTypeFilter}
            onChange={(e) => setDocTypeFilter(e.target.value)}
            aria-label="文档类型筛选"
          >
            <option value="">全部类型</option>
            <option value="tutorial">tutorial</option>
            <option value="api">api</option>
            <option value="requirements">requirements</option>
            <option value="general">general</option>
          </select>
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
              <p>点击左侧文档，查看该文档被切分成的所有向量片段（chunks）内容与元数据。</p>
            </div>
          )}

          {selected && chunksLoading && (
            <div className="field-label" style={{ padding: "20px 0" }}>加载中…</div>
          )}

          {selected && !chunksLoading && chunks.map((c) => (
            <div key={c.id} style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--panel)", padding: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                <span className="badge blue">#{c.chunk_index}</span>
                {c.meta.page != null && (
                  <span className="badge">第 {String(c.meta.page)} 页</span>
                )}
                <span className="badge" style={{ fontSize: 10, color: "var(--text-dim)" }}>
                  {c.content.length} 字符
                </span>
              </div>
              <pre style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 12.5,
                lineHeight: 1.7,
                color: "var(--text)",
                margin: 0,
                fontFamily: "inherit",
                borderLeft: "2px solid var(--border)",
                paddingLeft: 12,
              }}>
                {c.content}
              </pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
