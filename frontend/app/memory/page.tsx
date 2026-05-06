"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function loadUserId(): string {
  try { return localStorage.getItem("rag_user_id") || "demo"; } catch { return "demo"; }
}

type MemItem = { id: string; kind: string; content: string; created_at: string };

function MemoryContent() {
  const params = useSearchParams();
  const [userId, setUserId] = useState(() => params.get("user_id") ?? loadUserId());
  const [items, setItems] = useState<MemItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newKind, setNewKind] = useState<"fact" | "profile" | "decision">("fact");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/memory?user_id=${encodeURIComponent(userId)}`);
      if (!r.ok) return;
      const data = await r.json().catch(() => []);
      setItems(Array.isArray(data) ? data : []);
    } catch {
      // backend not ready
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [userId]);

  const forget = async (id: string) => {
    await fetch(`/api/memory/${id}?user_id=${encodeURIComponent(userId)}`, { method: "DELETE" });
    setItems((m) => m.filter((x) => x.id !== id));
  };

  const add = async () => {
    if (!newContent.trim()) return;
    setSaving(true);
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, kind: newKind, content: newContent.trim() }),
    });
    setNewContent("");
    await load();
    setSaving(false);
  };

  const kindColor: Record<string, string> = {
    fact: "blue",
    profile: "green",
    decision: "",
  };

  return (
    <div className="ingest-layout" style={{ maxWidth: 680 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2>长期记忆</h2>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <a href={`/kg?user_id=${encodeURIComponent(userId)}`} className="btn">🔗 知识图谱</a>
          <a href="/" className="btn">← 返回对话</a>
        </div>
      </div>
      <p className="field-label" style={{ marginBottom: 16 }}>
        跨会话可检索的用户记忆，对话前会按语义相似度注入 Prompt。
      </p>

      {/* user_id selector */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input className="text-input" value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="user_id" style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>刷新</button>
      </div>

      {/* add memory */}
      <div className="card" style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 12, padding: 14, marginBottom: 16 }}>
        <div className="field-label" style={{ marginBottom: 6 }}>手动添加记忆</div>
        <textarea
          className="text-input"
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
          placeholder="例如：用户是后端开发者，擅长 Java 和 Python"
          style={{ minHeight: 60 }}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <select className="text-input" style={{ width: "auto" }} value={newKind} onChange={(e) => setNewKind(e.target.value as "fact" | "profile" | "decision")}>
            <option value="fact">fact（事实）</option>
            <option value="profile">profile（身份）</option>
            <option value="decision">decision（决策）</option>
          </select>
          <button className="btn primary" onClick={add} disabled={saving || !newContent.trim()}>
            {saving ? "保存中…" : "添加"}
          </button>
        </div>
      </div>

      {/* list */}
      {loading && <div className="field-label">加载中…</div>}
      {!loading && items.length === 0 && (
        <div className="empty-state" style={{ padding: 30 }}>
          <div className="empty-state-icon" style={{ fontSize: 32 }}>🧠</div>
          <p>暂无记忆。说一句"记住，我是…"触发自动提取，或在上方手动添加。</p>
        </div>
      )}
      {items.map((m) => (
        <div key={m.id} style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--panel)", padding: 12, marginBottom: 8, display: "flex", gap: 10, alignItems: "flex-start" }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
              <span className={`badge ${kindColor[m.kind] ?? ""}`}>{m.kind}</span>
              <span className="badge" style={{ fontSize: 10 }}>{new Date(m.created_at).toLocaleString("zh-CN")}</span>
            </div>
            <div style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.6 }}>{m.content}</div>
          </div>
          <button className="btn danger" style={{ flexShrink: 0 }} onClick={() => forget(m.id)}>删除</button>
        </div>
      ))}
    </div>
  );
}

export default function MemoryPage() {
  return (
    <Suspense>
      <MemoryContent />
    </Suspense>
  );
}
