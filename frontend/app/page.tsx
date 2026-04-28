"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { consumeSse } from "@/lib/sse";

type Source = {
  chunk_id: string;
  source?: string | null;
  page?: number | null;
  score?: number;
  snippet?: string;
};

type ChatMsg = {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  streaming?: boolean;
};

type Session = { id: string; label: string };

const SESSION_KEY = (userId: string) => `rag_sessions_${userId}`;
const USER_ID_KEY = "rag_user_id";

let _uid = 0;
const uid = () => ++_uid;

function loadUserId(): string {
  try {
    return localStorage.getItem(USER_ID_KEY) || "demo";
  } catch {
    return "demo";
  }
}

function saveUserId(id: string) {
  try {
    localStorage.setItem(USER_ID_KEY, id);
  } catch {}
}

function loadSessions(userId: string): Session[] {
  try {
    const raw = localStorage.getItem(SESSION_KEY(userId));
    return raw ? (JSON.parse(raw) as Session[]) : [];
  } catch {
    return [];
  }
}

function saveSessions(userId: string, sessions: Session[]) {
  try {
    localStorage.setItem(SESSION_KEY(userId), JSON.stringify(sessions.slice(0, 50)));
  } catch {}
}

export default function HomePage() {
  const [userId, setUserId] = useState(loadUserId);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [histLoading, setHistLoading] = useState(false);
  const [health, setHealth] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [memToast, setMemToast] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  // 初始化：从 localStorage 恢复会话列表，同时从服务器同步
  useEffect(() => {
    const stored = loadSessions(userId);
    setSessions(stored);
    // 自动从服务器补全本地没有的历史会话
    syncSessionsFromServer(userId, stored).then((merged) => {
      if (merged.length > stored.length) {
        setSessions(merged);
        saveSessions(userId, merged);
      }
    });
  }, [userId]);

  const syncSessionsFromServer = async (uid: string, existing: Session[]): Promise<Session[]> => {
    try {
      const r = await fetch(`/api/sessions?user_id=${encodeURIComponent(uid)}&limit=50`);
      if (!r.ok) return existing;
      const data = await r.json() as { id: string; summary?: string | null; created_at: string }[];
      const existingIds = new Set(existing.map((s) => s.id));
      const newOnes: Session[] = data
        .filter((s) => !existingIds.has(s.id))
        .map((s) => ({
          id: s.id,
          label: s.summary?.trim() || `历史会话 ${new Date(s.created_at).toLocaleDateString("zh-CN")}`,
        }));
      if (newOnes.length === 0) return existing;
      return [...newOnes, ...existing];
    } catch {
      return existing;
    }
  };

  const syncNow = async () => {
    setSyncing(true);
    try {
      const merged = await syncSessionsFromServer(userId, sessions);
      setSessions(merged);
      saveSessions(userId, merged);
    } finally {
      setSyncing(false);
    }
  };

  // 消息变化时滚到底
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  };

  // 切换或恢复会话：从后端拉取历史消息
  const loadSession = useCallback(async (s: Session) => {
    setCurrentSession(s.id);
    setMessages([]);
    setHealth(null);
    setHistLoading(true);
    try {
      const r = await fetch(`/api/sessions/${s.id}/messages`);
      if (r.ok) {
        const data = await r.json() as { id: string; role: string; content: string }[];
        setMessages(
          data.map((m) => ({
            id: uid(),
            role: m.role as "user" | "assistant",
            content: m.content,
            sources: [],
          }))
        );
      }
    } finally {
      setHistLoading(false);
    }
  }, []);

  const startNewSession = useCallback(() => {
    setCurrentSession(null);
    setMessages([]);
    setHealth(null);
  }, []);

  const addSession = useCallback(
    (sess: Session, currentUserId: string) => {
      setSessions((prev) => {
        if (prev.find((s) => s.id === sess.id)) return prev;
        const next = [sess, ...prev];
        saveSessions(currentUserId, next);
        return next;
      });
    },
    []
  );

  const startRename = (s: Session, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(s.id);
    setEditLabel(s.label);
    setTimeout(() => editInputRef.current?.select(), 30);
  };

  const commitRename = async (id: string) => {
    const label = editLabel.trim();
    if (!label) { setEditingId(null); return; }
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, label } : s));
      saveSessions(userId, next);
      return next;
    });
    setEditingId(null);
    try {
      await fetch(`/api/sessions/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ summary: label }),
      });
    } catch {}
  };

  const deleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (deletingId === id) {
      setDeletingId(null);
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        saveSessions(userId, next);
        return next;
      });
      if (currentSession === id) {
        setCurrentSession(null);
        setMessages([]);
      }
      try { await fetch(`/api/sessions/${id}`, { method: "DELETE" }); } catch {}
    } else {
      setDeletingId(id);
      setTimeout(() => setDeletingId((cur) => (cur === id ? null : cur)), 3000);
    }
  };

  const exportMarkdown = () => {
    if (messages.length === 0) return;
    const sessionLabel = currentSession
      ? (sessions.find((s) => s.id === currentSession)?.label ?? currentSession.slice(0, 8))
      : "新对话";
    const lines: string[] = [`# ${sessionLabel}`, `> 导出时间：${new Date().toLocaleString("zh-CN")}`, ""];
    for (const msg of messages) {
      if (msg.streaming) continue;
      if (msg.role === "user") {
        lines.push(`**用户**\n\n${msg.content}`);
      } else {
        lines.push(`**助手**\n\n${msg.content}`);
        if (msg.sources && msg.sources.length > 0) {
          lines.push("\n**参考来源：**");
          msg.sources.forEach((s, i) => {
            const page = s.page != null ? ` 第${s.page}页` : "";
            const score = s.score != null ? ` (${(s.score * 100).toFixed(0)}%)` : "";
            lines.push(`- [S${i + 1}] ${s.source ?? "未知来源"}${page}${score}`);
            if (s.snippet) lines.push(`  > ${s.snippet.slice(0, 120).replace(/\n/g, " ")}`);
          });
        }
      }
      lines.push("\n---\n");
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${sessionLabel.slice(0, 20)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const pingHealth = async () => {
    setHealthLoading(true);
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      const t = await r.text();
      let pretty = t;
      try { pretty = JSON.stringify(JSON.parse(t), null, 2); } catch {}
      setHealth(`HTTP ${r.status}\n${pretty}`);
    } catch (e) {
      setHealth(String(e));
    } finally {
      setHealthLoading(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    const userMsg: ChatMsg = { id: uid(), role: "user", content: text };
    const assistantMsg: ChatMsg = { id: uid(), role: "assistant", content: "", sources: [], streaming: true };
    setMessages((m) => [...m, userMsg, assistantMsg]);

    const aId = assistantMsg.id;
    const payload: Record<string, unknown> = { user_id: userId, message: text, top_k: 8 };
    if (currentSession) payload.session_id = currentSession;

    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const t = await res.text();
      setMessages((m) =>
        m.map((msg) => (msg.id === aId ? { ...msg, content: `请求失败 ${res.status}: ${t}`, streaming: false } : msg))
      );
      setBusy(false);
      return;
    }

    const capturedUserId = userId;
    await consumeSse(res, (event, data) => {
      if (event === "sources" && data && typeof data === "object") {
        const d = data as { session_id?: string; sources?: Source[] };
        if (d.session_id) {
          const sid = d.session_id;
          setCurrentSession(sid);
          addSession(
            { id: sid, label: text.slice(0, 24) + (text.length > 24 ? "…" : "") },
            capturedUserId
          );
        }
        const srcs = d.sources ?? [];
        setMessages((m) =>
          m.map((msg) => (msg.id === aId ? { ...msg, sources: srcs } : msg))
        );
      }
      if (event === "token" && data && typeof data === "object") {
        const delta = (data as { delta?: string }).delta ?? "";
        if (!delta) return;
        setMessages((m) =>
          m.map((msg) => (msg.id === aId ? { ...msg, content: msg.content + delta } : msg))
        );
      }
      if (event === "final" && data && typeof data === "object") {
        const writes = (data as { memory_writes?: string[] }).memory_writes ?? [];
        if (writes.length > 0) {
          setMemToast(`💾 已记住：${writes[0]}`);
          setTimeout(() => setMemToast(null), 5000);
        }
      }
      if (event === "error" && data && typeof data === "object") {
        const msg = (data as { message?: string }).message ?? "未知错误";
        setMessages((m) =>
          m.map((s) => (s.id === aId ? { ...s, content: `错误：${msg}`, streaming: false } : s))
        );
      }
    });

    setMessages((m) => m.map((msg) => (msg.id === aId ? { ...msg, streaming: false } : msg)));
    setBusy(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const isEmpty = messages.length === 0;

  return (
    <div className="app-shell">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>RAG Agent</h1>
          <p>本地知识库对话</p>
        </div>

        <div className="sidebar-body">
          <div className="sidebar-section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingRight: 10 }}>
            <span>会话</span>
            <button
              onClick={syncNow}
              disabled={syncing}
              title="从服务器同步历史会话"
              className={`sync-btn${syncing ? " spinning" : ""}`}
            >
              ↻
            </button>
          </div>
          <div
            className={`sidebar-session ${!currentSession ? "active" : ""}`}
            onClick={startNewSession}
          >
            <div className="sidebar-session-icon">+</div>
            <span>新对话</span>
          </div>
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`sidebar-session ${currentSession === s.id ? "active" : ""}`}
              onClick={() => editingId !== s.id && loadSession(s)}
              onDoubleClick={(e) => startRename(s, e)}
            >
              <div className="sidebar-session-icon">💬</div>
              {editingId === s.id ? (
                <input
                  ref={editInputRef}
                  className="session-rename-input"
                  placeholder="会话名称"
                  aria-label="重命名会话"
                  value={editLabel}
                  onChange={(e) => setEditLabel(e.target.value)}
                  onBlur={() => commitRename(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(s.id);
                    if (e.key === "Escape") setEditingId(null);
                    e.stopPropagation();
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span className="session-label">{s.label}</span>
              )}
              <div className="session-actions">
                <button
                  className="session-action-btn"
                  title="重命名（双击也可）"
                  onClick={(e) => startRename(s, e)}
                >✏️</button>
                <button
                  className={`session-action-btn${deletingId === s.id ? " danger" : ""}`}
                  title={deletingId === s.id ? "再次点击确认删除" : "删除会话"}
                  onClick={(e) => deleteSession(s.id, e)}
                >
                  {deletingId === s.id ? "确认" : "🗑"}
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <div className="field-label" style={{ marginBottom: 6 }}>user_id</div>
          <input
            className="userid-input"
            aria-label="用户 ID"
            placeholder="demo"
            value={userId}
            onChange={(e) => { setUserId(e.target.value); saveUserId(e.target.value); }}
          />
          <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
            <a href="/ingest" className="btn" style={{ width: "100%", justifyContent: "center" }}>📄 文档入库</a>
            <a href="/documents" className="btn" style={{ width: "100%", justifyContent: "center" }}>🔍 查看文档库</a>
            <a href={`/memory?user_id=${userId}`} className="btn" style={{ width: "100%", justifyContent: "center" }}>🧠 查看记忆</a>
            <a href="/stats" className="btn" style={{ width: "100%", justifyContent: "center" }}>📊 系统统计</a>
            <button className="btn" style={{ width: "100%", justifyContent: "center" }} onClick={pingHealth} disabled={healthLoading}>
              {healthLoading ? "检查中…" : "⚡ 健康检查"}
            </button>
          </div>
        </div>
      </aside>

      {/* ── Main ── */}
      <div className="main">
        <div className="topbar">
          <span className="topbar-title">
            {currentSession
              ? (sessions.find((s) => s.id === currentSession)?.label ?? `会话 ${currentSession.slice(0, 8)}…`)
              : "新对话"}
          </span>
          {memToast && (
            <span className="badge green" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {memToast}
            </span>
          )}
          {messages.length > 0 && !busy && (
            <button className="btn" title="导出为 Markdown" onClick={exportMarkdown} style={{ fontSize: 11, padding: "4px 8px" }}>
              ↓ 导出 MD
            </button>
          )}
          {currentSession && <span className="badge blue">pgvector</span>}
          <span className="badge green">Ollama · qwen2.5:7b</span>
        </div>

        {health && <pre className="health-pop">{health}</pre>}

        <div className="chat-area">
          {histLoading ? (
            <div className="empty-state">
              <div className="empty-state-icon" style={{ fontSize: 32 }}>⏳</div>
              <p>正在加载历史消息…</p>
            </div>
          ) : isEmpty ? (
            <div className="empty-state">
              <div className="empty-state-icon">🧠</div>
              <h3>开始你的知识库对话</h3>
              <p>先在左侧「文档入库」上传文档，然后在这里提问，助手会检索相关片段并给出带引用的回答。</p>
            </div>
          ) : (
            messages.map((msg) => <MessageRow key={msg.id} msg={msg} />)
          )}
          <div ref={chatEndRef} />
        </div>

        <div className="input-bar">
          <div className="input-wrap">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => { setInput(e.target.value); autoResize(); }}
              onKeyDown={onKeyDown}
              placeholder="输入问题… (Enter 发送，Shift+Enter 换行)"
              rows={1}
            />
            <button className="send-btn" onClick={send} disabled={!input.trim() || busy}>↑</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function MarkdownContent({ content, sources, onCiteClick }: {
  content: string;
  sources?: Source[];
  onCiteClick?: (idx: number) => void;
}) {
  // 把 [S1] [S2] 替换为可点击徽章（先占位，再在 text 节点里处理）
  const renderText = (text: string) => {
    const parts = text.split(/(\[S\d+\])/g);
    return parts.map((part, i) => {
      const m = part.match(/^\[S(\d+)\]$/);
      if (m) {
        const idx = parseInt(m[1]) - 1;
        const src = sources?.[idx];
        return (
          <button
            key={i}
            className="cite-badge"
            title={src ? `${src.source ?? ""}${src.page != null ? ` p.${src.page}` : ""}` : part}
            onClick={() => onCiteClick?.(idx)}
          >
            {part}
          </button>
        );
      }
      return part;
    });
  };

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className ?? "");
          const isBlock = !!match;
          return isBlock ? (
            <SyntaxHighlighter
              style={oneDark}
              language={match[1]}
              PreTag="div"
              customStyle={{ borderRadius: 6, fontSize: 12, margin: "6px 0" }}
            >
              {String(children).replace(/\n$/, "")}
            </SyntaxHighlighter>
          ) : (
            <code className="md-inline-code" {...props}>{children}</code>
          );
        },
        a({ href, children }) {
          return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
        },
        p({ children }) {
          // 把段落里的文本节点过一遍引用解析
          const processed = Array.isArray(children)
            ? children.flatMap((child) =>
                typeof child === "string" ? renderText(child) : [child]
              )
            : typeof children === "string" ? renderText(children) : children;
          return <p>{processed}</p>;
        },
        li({ children }) {
          const processed = Array.isArray(children)
            ? children.flatMap((child) =>
                typeof child === "string" ? renderText(child) : [child]
              )
            : typeof children === "string" ? renderText(children) : children;
          return <li>{processed}</li>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function MessageRow({ msg }: { msg: ChatMsg }) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState<number | null>(null);
  const hasSources = (msg.sources?.length ?? 0) > 0;

  const handleCiteClick = (idx: number) => {
    setSourcesOpen(true);
    setHighlightIdx(idx);
    setTimeout(() => setHighlightIdx(null), 2000);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {msg.role === "assistant" && hasSources && (
        <div className="sources-block">
          <button className="sources-toggle" onClick={() => setSourcesOpen((o) => !o)}>
            📎 {msg.sources!.length} 个知识片段 {sourcesOpen ? "▲" : "▼"}
          </button>
          {sourcesOpen && (
            <div className="sources-list">
              {msg.sources!.map((s, i) => (
                <div key={s.chunk_id} className={`source-card${highlightIdx === i ? " highlighted" : ""}`}>
                  <div className="source-card-header">
                    <span className="source-card-file">[S{i + 1}] {s.source ?? "未知来源"}</span>
                    {s.page != null && <span className="source-card-meta">第 {s.page} 页</span>}
                    {s.score != null && <span className="source-card-meta">相似度 {(s.score * 100).toFixed(0)}%</span>}
                  </div>
                  {s.snippet && <div className="source-card-snippet">{s.snippet}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className={`msg-row ${msg.role}`}>
        <div className={`msg-avatar ${msg.role}`}>
          {msg.role === "user" ? "🧑" : "🤖"}
        </div>
        <div className="msg-content">
          {msg.role === "assistant" && msg.streaming && !msg.content ? (
            <div className="msg-bubble assistant">
              <div className="typing-dots"><span /><span /><span /></div>
            </div>
          ) : (
            <div className={`msg-bubble ${msg.role}${msg.streaming ? " streaming" : ""}`}>
              {msg.role === "assistant"
                ? <MarkdownContent content={msg.content} sources={msg.sources} onCiteClick={handleCiteClick} />
                : msg.content}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
