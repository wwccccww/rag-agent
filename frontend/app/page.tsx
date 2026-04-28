"use client";

import { useMemo, useState } from "react";
import { consumeSse } from "@/lib/sse";

type ChatMsg = { role: "user" | "assistant"; content: string };
type Source = { chunk_id: string; source?: string | null; page?: number | null; score?: number; snippet?: string };

export default function HomePage() {
  const [userId, setUserId] = useState("demo");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<string>("");

  const canSend = useMemo(() => input.trim().length > 0 && !busy, [input, busy]);

  async function pingHealth() {
    const r = await fetch("/api/health", { cache: "no-store" });
    const t = await r.text();
    setHealth(`${r.status} ${t}`);
  }

  async function send() {
    const text = input.trim();
    if (!text) return;
    setBusy(true);
    setInput("");
    setSources([]);
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);

    const payload: Record<string, unknown> = { user_id: userId, message: text, top_k: 8 };
    if (sessionId) payload.session_id = sessionId;

    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const t = await res.text();
      setMessages((m) => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last?.role === "assistant") last.content = `错误：${res.status}\n${t}`;
        return copy;
      });
      setBusy(false);
      return;
    }

    await consumeSse(res, (event, data) => {
      if (event === "sources" && data && typeof data === "object") {
        const sid = (data as { session_id?: string }).session_id;
        if (sid) setSessionId(sid);
        const src = (data as { sources?: Source[] }).sources ?? [];
        setSources(src);
      }
      if (event === "token" && data && typeof data === "object") {
        const delta = (data as { delta?: string }).delta ?? "";
        if (!delta) return;
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") last.content += delta;
          return copy;
        });
      }
      if (event === "error" && data && typeof data === "object") {
        const msg = (data as { message?: string }).message ?? "unknown error";
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant" && !last.content) last.content = `错误：${msg}`;
          else copy.push({ role: "assistant", content: `错误：${msg}` });
          return copy;
        });
      }
    });

    setBusy(false);
  }

  function newSession() {
    setSessionId(null);
    setMessages([]);
    setSources([]);
  }

  return (
    <div className="container">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2 style={{ margin: 0 }}>RAG 流式对话</h2>
          <p className="muted" style={{ marginTop: 8 }}>
            后端 FastAPI + Ollama；向量库 Postgres(pgvector)；本页通过 Next.js BFF 代理 SSE。
          </p>
        </div>
        <a href="/ingest">去入库</a>
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <div className="row">
          <div style={{ flex: 1, minWidth: 220 }}>
            <div className="muted" style={{ marginBottom: 6 }}>
              user_id（长期记忆维度）
            </div>
            <input type="text" value={userId} onChange={(e) => setUserId(e.target.value)} />
          </div>
          <div style={{ alignSelf: "flex-end" }}>
            <span className="pill">session: {sessionId ? sessionId.slice(0, 8) + "…" : "new"}</span>
          </div>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button type="button" onClick={newSession} disabled={busy}>
            新会话
          </button>
          <button type="button" onClick={pingHealth} disabled={busy}>
            健康检查
          </button>
        </div>
        {health ? (
          <pre className="muted" style={{ marginTop: 10, whiteSpace: "pre-wrap" }}>
            {health}
          </pre>
        ) : null}
      </div>

      {sources.length ? (
        <div className="card sources" style={{ marginTop: 12 }}>
          <div className="muted">本轮检索 sources</div>
          {sources.map((s) => (
            <div key={s.chunk_id} className="source">
              <div>
                <strong>{s.source ?? "unknown"}</strong>{" "}
                {typeof s.page === "number" ? <span className="muted">p.{s.page}</span> : null}{" "}
                {typeof s.score === "number" ? <span className="muted">score {s.score.toFixed(3)}</span> : null}
              </div>
              <div className="muted" style={{ marginTop: 6 }}>
                {s.snippet}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div style={{ marginTop: 12 }}>
        {messages.map((m, idx) => (
          <div key={idx} className={`msg ${m.role}`}>
            <div className="muted" style={{ marginBottom: 6 }}>
              {m.role}
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="输入问题…" />
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={send} disabled={!canSend}>
            发送
          </button>
          {busy ? <span className="muted">生成中…</span> : null}
        </div>
      </div>
    </div>
  );
}
