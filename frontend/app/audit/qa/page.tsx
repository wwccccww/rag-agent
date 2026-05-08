"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

type QaAuditItem = {
  id: string;
  created_at: string;
  user_id: string;
  session_id: string | null;
  kb_collection: string;
  mode: string;
  request_id: string | null;
  user_message: string;
  assistant_preview: string | null;
  cited_chunk_ids: string[];
  sources_count: number;
};

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max)}…`;
}

export default function QaAuditPage() {
  const [userId, setUserId] = useState("demo");
  const [mode, setMode] = useState("");
  const [kbCollection, setKbCollection] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [requestId, setRequestId] = useState("");
  const [limit, setLimit] = useState(100);
  const [items, setItems] = useState<QaAuditItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (userId.trim()) p.set("user_id", userId.trim());
    if (mode.trim()) p.set("mode", mode.trim());
    if (kbCollection.trim()) p.set("kb_collection", kbCollection.trim());
    if (sessionId.trim()) p.set("session_id", sessionId.trim());
    if (requestId.trim()) p.set("request_id", requestId.trim());
    p.set("limit", String(limit));
    return p.toString();
  }, [userId, mode, kbCollection, sessionId, requestId, limit]);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`/api/audit/qa?${query}`, { cache: "no-store" });
      const t = await r.text();
      if (!r.ok) throw new Error(t || `HTTP ${r.status}`);
      const j = JSON.parse(t) as unknown;
      setItems(Array.isArray(j) ? (j as QaAuditItem[]) : []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="audit-page audit-layout">
      <div className="kg-header">
        <h2>问答审计</h2>
        <div className="kg-header-actions">
          <a href="/" className="btn">← 返回对话</a>
          <a href="/audit" className="btn">🧾 工具审计</a>
          <a href="/stats" className="btn">📊 系统统计</a>
        </div>
      </div>
      <p className="field-label kg-intro">
        查看 <code>qa_audit_logs</code>：每轮对话的用户问题、分区、模式、检索引用片段（<code>chunk_id</code>）等。
      </p>

      <div className="audit-toolbar">
        <input className="text-input" value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="user_id" />
        <select className="text-input" value={mode} onChange={(e) => setMode(e.target.value)} title="按 mode 过滤（可选）">
          <option value="">mode（全部）</option>
          <option value="rag">rag</option>
          <option value="agent">agent</option>
          <option value="plan">plan</option>
          <option value="multi">multi</option>
        </select>
        <input
          className="text-input"
          value={kbCollection}
          onChange={(e) => setKbCollection(e.target.value)}
          placeholder="kb_collection（可选）"
        />
        <input
          className="text-input audit-mono"
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          placeholder="session_id UUID（可选）"
        />
        <input className="text-input audit-mono" value={requestId} onChange={(e) => setRequestId(e.target.value)} placeholder="request_id（可选）" />
        <input
          className="text-input"
          value={String(limit)}
          onChange={(e) => setLimit(Math.max(1, Math.min(500, parseInt(e.target.value || "100", 10) || 100)))}
          placeholder="limit"
        />
        <button className="btn" type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>

      {err && <div className="kg-error" role="alert">{err}</div>}

      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>mode</th>
              <th>分区</th>
              <th>用户问题</th>
              <th>引用数</th>
              <th>request_id</th>
              <th>展开</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const isOpen = expanded === it.id;
              return (
                <Fragment key={it.id}>
                  <tr className="audit-row ok">
                    <td>{fmtTs(it.created_at)}</td>
                    <td><span className="audit-pill ok">{it.mode}</span></td>
                    <td><code>{it.kb_collection}</code></td>
                    <td className="qa-msg-cell" title={it.user_message}>{truncate(it.user_message, 120)}</td>
                    <td>{it.sources_count}</td>
                    <td className="audit-mono">{it.request_id ?? "-"}</td>
                    <td>
                      <button className="btn" type="button" onClick={() => setExpanded((cur) => (cur === it.id ? null : it.id))}>
                        {isOpen ? "收起" : "详情"}
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="audit-detail-row">
                      <td colSpan={7}>
                        <div className="audit-detail-grid">
                          <div>
                            <div className="field-label">user_message</div>
                            <pre className="audit-pre qa-pre-wrap">{it.user_message}</pre>
                          </div>
                          <div>
                            <div className="field-label">assistant_preview</div>
                            <pre className="audit-pre qa-pre-wrap">{String(it.assistant_preview ?? "")}</pre>
                            <div className="field-label audit-mt10">cited_chunk_ids</div>
                            <pre className="audit-pre">{JSON.stringify(it.cited_chunk_ids ?? [], null, 2)}</pre>
                            <div className="field-label audit-mt10">session_id</div>
                            <pre className="audit-pre">{String(it.session_id ?? "-")}</pre>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {items.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="field-label">
                  暂无数据。先在任意对话模式完成一轮问答后再刷新（需开启后端 <code>QA_AUDIT_ENABLED</code>）。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
