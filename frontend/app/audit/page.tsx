"use client";

import { useEffect, useMemo, useState } from "react";

type ToolAuditItem = {
  id: string;
  created_at: string;
  user_id: string;
  session_id: string | null;
  mode: string;
  request_id: string | null;
  worker?: string | null;
  tool: string;
  status: string;
  elapsed_ms: number | null;
  sources_count: number;
  tool_args: Record<string, unknown>;
  error?: string | null;
  result_preview?: string | null;
};

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}

export default function AuditPage() {
  const [userId, setUserId] = useState("demo");
  const [mode, setMode] = useState("");
  const [tool, setTool] = useState("");
  const [status, setStatus] = useState("");
  const [requestId, setRequestId] = useState("");
  const [worker, setWorker] = useState("");
  const [limit, setLimit] = useState(100);
  const [items, setItems] = useState<ToolAuditItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (userId.trim()) p.set("user_id", userId.trim());
    if (mode.trim()) p.set("mode", mode.trim());
    if (tool.trim()) p.set("tool", tool.trim());
    if (status.trim()) p.set("status", status.trim());
    if (requestId.trim()) p.set("request_id", requestId.trim());
    if (worker.trim()) p.set("worker", worker.trim());
    p.set("limit", String(limit));
    return p.toString();
  }, [userId, mode, tool, status, requestId, worker, limit]);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`/api/audit/tools?${query}`, { cache: "no-store" });
      const t = await r.text();
      if (!r.ok) throw new Error(t || `HTTP ${r.status}`);
      const j = JSON.parse(t) as unknown;
      setItems(Array.isArray(j) ? (j as ToolAuditItem[]) : []);
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
        <h2>工具审计</h2>
        <div className="kg-header-actions">
          <a href="/" className="btn">← 返回对话</a>
          <a href="/stats" className="btn">📊 系统统计</a>
        </div>
      </div>
      <p className="field-label kg-intro">
        查看 <code>tool_audit_logs</code> 中的工具调用记录（权限拒绝/失败/耗时/参数/结果预览）。
      </p>

      <div className="audit-toolbar">
        <input className="text-input" value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="user_id" />
        <select className="text-input" value={mode} onChange={(e) => setMode(e.target.value)} title="按 mode 过滤（可选）">
          <option value="">mode（全部）</option>
          <option value="rag">rag</option>
          <option value="agent">agent</option>
          <option value="plan">plan</option>
          <option value="multi">multi</option>
          <option value="system">system</option>
        </select>
        <select className="text-input" value={tool} onChange={(e) => setTool(e.target.value)} title="按 tool 过滤（可选）">
          <option value="">tool（全部）</option>
          <option value="search_knowledge_base">search_knowledge_base</option>
          <option value="recall_user_memory">recall_user_memory</option>
          <option value="web_search">web_search</option>
          <option value="fetch_url">fetch_url</option>
          <option value="python_repl">python_repl</option>
          <option value="calculate">calculate</option>
          <option value="get_current_datetime">get_current_datetime</option>
        </select>
        <select className="text-input" value={status} onChange={(e) => setStatus(e.target.value)} title="按 status 过滤（可选）">
          <option value="">status（全部）</option>
          <option value="ok">ok</option>
          <option value="denied">denied</option>
          <option value="error">error</option>
          <option value="timeout">timeout</option>
        </select>
        <input className="text-input" value={requestId} onChange={(e) => setRequestId(e.target.value)} placeholder="request_id（可选）" />
        <select className="text-input" value={worker} onChange={(e) => setWorker(e.target.value)} title="按 worker 过滤（可选）">
          <option value="">worker（全部）</option>
          <option value="supervisor">supervisor</option>
          <option value="retriever">retriever</option>
          <option value="solver">solver</option>
          <option value="critic">critic</option>
          <option value="synth">synth</option>
        </select>
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
              <th>tool</th>
              <th>status</th>
              <th>耗时</th>
              <th>sources</th>
              <th>request_id</th>
              <th>worker</th>
              <th>展开</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const isOpen = expanded === it.id;
              return (
                <>
                  <tr key={it.id} className={`audit-row ${it.status}`}>
                    <td>{fmtTs(it.created_at)}</td>
                    <td>{it.mode}</td>
                    <td><code>{it.tool}</code></td>
                    <td><span className={`audit-pill ${it.status}`}>{it.status}</span></td>
                    <td>{it.elapsed_ms == null ? "-" : `${Math.round(it.elapsed_ms)}ms`}</td>
                    <td>{it.sources_count}</td>
                    <td className="audit-mono">{it.request_id ?? "-"}</td>
                    <td className="audit-mono">{it.worker ?? "-"}</td>
                    <td>
                      <button className="btn" type="button" onClick={() => setExpanded((cur) => (cur === it.id ? null : it.id))}>
                        {isOpen ? "收起" : "详情"}
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr key={`${it.id}-detail`} className="audit-detail-row">
                      <td colSpan={9}>
                        <div className="audit-detail-grid">
                          <div>
                            <div className="field-label">tool_args</div>
                            <pre className="audit-pre">{JSON.stringify(it.tool_args ?? {}, null, 2)}</pre>
                          </div>
                          <div>
                            <div className="field-label">error</div>
                            <pre className="audit-pre">{String(it.error ?? "")}</pre>
                            <div className="field-label audit-mt10">result_preview</div>
                            <pre className="audit-pre">{String(it.result_preview ?? "")}</pre>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
            {items.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="field-label">暂无数据。先在 Agent/规划 模式触发一次工具调用后再刷新。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

