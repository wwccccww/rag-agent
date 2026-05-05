"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import {
  PRESET_DOC_TYPES,
  PRESET_DOC_TYPE_SET,
  slugDocType,
  USER_ID_KEY,
  loadDocShortcutsForUser,
  saveDocShortcutsForUser,
  MAX_DOC_SHORTCUTS,
} from "@/lib/kb";
import { consumeSse } from "@/lib/sse";

type Source = {
  chunk_id: string;
  source?: string | null;
  page?: number | null;
  section_heading?: string | null;
  score?: number;
  snippet?: string;
};

type AgentStep = {
  step: number;
  tool: string;
  icon: string;
  label: string;
  status: "calling" | "done";
  args?: Record<string, string>;
  result_summary?: string;
  source_count?: number;
  elapsed_ms?: number;
  reasoning?: string;
};

type PlanStep = {
  id: number;
  description: string;
  tool: string | null;
  tool_args?: Record<string, string>;
  purpose?: string;
  /** 前端渲染状态：pending/running/done/error */
  uiStatus?: "pending" | "running" | "done" | "error";
  elapsed_ms?: number;
};

type ChatMode = "rag" | "agent" | "plan";

type MsgStats = { tokens: number; tok_per_sec: number };

type ChatMsg = {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  streaming?: boolean;
  createdAt?: string;
  agentSteps?: AgentStep[];
  chatMode?: ChatMode;
  stats?: MsgStats;
  /** Plan & Execute 模式专属 */
  planGoal?: string;
  planSteps?: PlanStep[];
};

type Session = { id: string; label: string };

const SESSION_KEY = (userId: string) => `rag_sessions_${userId}`;
const KB_COLLECTION_KEY = "rag_kb_collection";
const DOC_TYPES_KEY = (uid: string) => `rag_doc_types_${uid}`;

function loadDocTypesForUser(uid: string): string[] {
  try {
    const raw = localStorage.getItem(DOC_TYPES_KEY(uid));
    if (!raw) return [];
    const p = JSON.parse(raw) as unknown;
    if (!Array.isArray(p)) return [];
    const out: string[] = [];
    for (const x of p) {
      if (typeof x !== "string") continue;
      const s = slugDocType(x);
      if (s && !out.includes(s)) out.push(s);
    }
    return out.slice(0, 8);
  } catch {
    return [];
  }
}

function saveDocTypesForUser(uid: string, types: string[]) {
  try {
    localStorage.setItem(DOC_TYPES_KEY(uid), JSON.stringify(types.slice(0, 8)));
  } catch {}
}

function loadKbCollection(): string {
  try {
    return localStorage.getItem(KB_COLLECTION_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveKbCollection(v: string) {
  try {
    localStorage.setItem(KB_COLLECTION_KEY, v);
  } catch {}
}

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
  const [chatMode, setChatMode] = useState<ChatMode>("rag");
  const [syncing, setSyncing] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [kbCollection, setKbCollection] = useState(loadKbCollection);
  const [activeDocTypes, setActiveDocTypes] = useState<string[]>([]);
  const [docTypeShortcuts, setDocTypeShortcuts] = useState<string[]>([]);
  const [catalogDocTypes, setCatalogDocTypes] = useState<string[]>([]);
  const [customTypeDraft, setCustomTypeDraft] = useState("");
  const [purgingSessions, setPurgingSessions] = useState(false);
  const [docTypeModalOpen, setDocTypeModalOpen] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

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

  useEffect(() => {
    const act = loadDocTypesForUser(userId);
    setActiveDocTypes(act);
    let shorts = loadDocShortcutsForUser(userId);
    for (const t of act) {
      if (!PRESET_DOC_TYPE_SET.has(t) && !shorts.includes(t)) {
        shorts = [t, ...shorts].slice(0, MAX_DOC_SHORTCUTS);
      }
    }
    saveDocShortcutsForUser(userId, shorts);
    setDocTypeShortcuts(shorts);
  }, [userId]);

  useEffect(() => {
    if (!docTypeModalOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDocTypeModalOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [docTypeModalOpen]);

  useEffect(() => {
    if (!docTypeModalOpen) return;
    let cancelled = false;
    void (async () => {
      try {
        let url = "/api/documents/catalog/doc-types";
        if (kbCollection.trim()) {
          url += `?kb_collection=${encodeURIComponent(kbCollection.trim())}`;
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
  }, [docTypeModalOpen, kbCollection]);

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
        type RawMsg = { id: string; role: string; content: string; created_at?: string; extra?: { agent_steps?: AgentStep[] } | null };
        const data = await r.json() as RawMsg[];
        setMessages(
          data.map((m) => {
            const steps = m.extra?.agent_steps;
            return {
              id: uid(),
              role: m.role as "user" | "assistant",
              content: m.content,
              sources: [],
              createdAt: m.created_at,
              agentSteps: steps && steps.length > 0 ? steps : undefined,
              agentMode: steps && steps.length > 0 ? true : undefined,
            };
          })
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

  // 每次发消息后把当前会话移到列表顶部
  const bumpSession = useCallback((sessionId: string, currentUserId: string) => {
    setSessions((prev) => {
      const idx = prev.findIndex((s) => s.id === sessionId);
      if (idx <= 0) return prev; // 已经在最前或不存在，无需移动
      const next = [prev[idx], ...prev.slice(0, idx), ...prev.slice(idx + 1)];
      saveSessions(currentUserId, next);
      return next;
    });
  }, []);

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

  const purgeAllSessions = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (sessions.length === 0) return;
    if (!window.confirm(`将删除全部 ${sessions.length} 个会话（含服务器上的消息），不可恢复。确定？`)) return;
    setPurgingSessions(true);
    try {
      for (const s of sessions) {
        try {
          await fetch(`/api/sessions/${s.id}`, { method: "DELETE" });
        } catch {
          /* ignore */
        }
      }
      setSessions([]);
      saveSessions(userId, []);
      setCurrentSession(null);
      setMessages([]);
      setDeletingId(null);
      setEditingId(null);
    } finally {
      setPurgingSessions(false);
    }
  };

  const toggleDocTypeFilter = (slug: string) => {
    setActiveDocTypes((prev) => {
      const next = prev.includes(slug) ? prev.filter((t) => t !== slug) : [...prev, slug].slice(0, 8);
      saveDocTypesForUser(userId, next);
      return next;
    });
  };

  const addCustomDocTypeFilter = () => {
    const t = slugDocType(customTypeDraft);
    if (!t) {
      window.alert(
        "无法解析为合法类型：1–32 位小写字母、数字、下划线、连字符（空格会变为连字符）；纯中文等符号需先转成英文标识。"
      );
      return;
    }
    setActiveDocTypes((prev) => {
      if (prev.includes(t)) return prev;
      const next = [...prev, t].slice(0, 8);
      saveDocTypesForUser(userId, next);
      return next;
    });
    if (!PRESET_DOC_TYPE_SET.has(t)) {
      setDocTypeShortcuts((prev) => {
        if (prev.includes(t)) return prev;
        const next = [t, ...prev].slice(0, MAX_DOC_SHORTCUTS);
        saveDocShortcutsForUser(userId, next);
        return next;
      });
    }
    setCustomTypeDraft("");
  };

  const removeDocTypeFilter = (slug: string) => {
    setActiveDocTypes((prev) => {
      const next = prev.filter((x) => x !== slug);
      saveDocTypesForUser(userId, next);
      return next;
    });
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

  const stopGeneration = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages((m) => m.map((msg) => (msg.streaming ? { ...msg, streaming: false } : msg)));
    setBusy(false);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    const now = new Date().toISOString();
    const userMsg: ChatMsg = { id: uid(), role: "user", content: text, createdAt: now };
    const assistantMsg: ChatMsg = {
      id: uid(), role: "assistant", content: "", sources: [],
      streaming: true, createdAt: now,
      chatMode,
      agentSteps: chatMode !== "rag" ? [] : undefined,
      planSteps: chatMode === "plan" ? [] : undefined,
    };
    setMessages((m) => [...m, userMsg, assistantMsg]);

    const aId = assistantMsg.id;
    const payload: Record<string, unknown> = { user_id: userId, message: text, top_k: 8 };
    if (currentSession) payload.session_id = currentSession;
    if (kbCollection.trim()) payload.kb_collection = kbCollection.trim();
    const docTypesPayload = activeDocTypes
      .map((x) => slugDocType(x))
      .filter((x): x is string => !!x)
      .filter((x, i, a) => a.indexOf(x) === i);
    if (docTypesPayload.length > 0) payload.doc_types = docTypesPayload;

    const endpoint =
      chatMode === "agent" ? "/api/chat/agent/stream" :
      chatMode === "plan"  ? "/api/chat/plan_execute/stream" :
      "/api/chat/stream";
    const controller = new AbortController();
    abortRef.current = controller;

    let res: Response;
    try {
      res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") { setBusy(false); return; }
      setMessages((m) =>
        m.map((msg) => (msg.id === aId ? { ...msg, content: `网络错误：${String(e)}`, streaming: false } : msg))
      );
      setBusy(false);
      return;
    }

    if (!res.ok) {
      const t = await res.text();
      setMessages((m) =>
        m.map((msg) => (msg.id === aId ? { ...msg, content: `请求失败 ${res.status}: ${t}`, streaming: false } : msg))
      );
      setBusy(false);
      return;
    }

    const capturedUserId = userId;
    let capturedSessionId = currentSession;
    await consumeSse(res, (event, data) => {
      // ── Plan & Execute 专属事件 ──────────────────────────────────────
      if (event === "plan" && data && typeof data === "object") {
        const d = data as { goal?: string; steps?: PlanStep[] };
        const steps = (d.steps ?? []).map((s) => ({ ...s, uiStatus: "pending" as const }));
        setMessages((m) =>
          m.map((msg) =>
            msg.id === aId
              ? { ...msg, planGoal: d.goal ?? "", planSteps: steps }
              : msg
          )
        );
      }
      if (event === "plan_step_start" && data && typeof data === "object") {
        const d = data as { step_id?: number };
        setMessages((m) =>
          m.map((msg) => {
            if (msg.id !== aId || !msg.planSteps) return msg;
            return {
              ...msg,
              planSteps: msg.planSteps.map((s) =>
                s.id === d.step_id ? { ...s, uiStatus: "running" as const } : s
              ),
            };
          })
        );
      }
      if (event === "plan_step_done" && data && typeof data === "object") {
        const d = data as { step_id?: number; success?: boolean; elapsed_ms?: number };
        setMessages((m) =>
          m.map((msg) => {
            if (msg.id !== aId || !msg.planSteps) return msg;
            return {
              ...msg,
              planSteps: msg.planSteps.map((s) =>
                s.id === d.step_id
                  ? {
                      ...s,
                      uiStatus: (d.success !== false ? "done" : "error") as "done" | "error",
                      elapsed_ms: d.elapsed_ms,
                    }
                  : s
              ),
            };
          })
        );
      }
      // ── Agent 工具步骤（agent 和 plan 模式共用）─────────────────────
      if (event === "agent_step" && data && typeof data === "object") {
        const step = data as AgentStep;
        setMessages((m) =>
          m.map((msg) => {
            if (msg.id !== aId) return msg;
            const existing = msg.agentSteps ?? [];
            const idx = existing.findIndex(
              (s) => s.step === step.step && s.tool === step.tool
            );
            const updated =
              idx >= 0
                ? [...existing.slice(0, idx), step, ...existing.slice(idx + 1)]
                : [...existing, step];
            return { ...msg, agentSteps: updated };
          })
        );
      }
      if (event === "sources" && data && typeof data === "object") {
        const d = data as { session_id?: string; sources?: Source[] };
        if (d.session_id) {
          const sid = d.session_id;
          capturedSessionId = sid;
          setCurrentSession(sid);
          addSession(
            { id: sid, label: text.slice(0, 24) + (text.length > 24 ? "…" : "") },
            capturedUserId
          );
          bumpSession(sid, capturedUserId);
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
        const d = data as {
          memory_writes?: string[];
          session_title?: string;
          stats?: MsgStats;
          assistant_content?: string;
        };
        const writes = d.memory_writes ?? [];
        if (writes.length > 0) {
          setMemToast(`💾 已记住：${writes[0]}`);
          setTimeout(() => setMemToast(null), 5000);
        }
        if (d.session_title) {
          const title = d.session_title;
          setSessions((prev) => {
            const next = prev.map((s) => (s.id === capturedSessionId ? { ...s, label: title } : s));
            saveSessions(capturedUserId, next);
            return next;
          });
        }
        if (typeof d.assistant_content === "string" && d.assistant_content.length > 0) {
          setMessages((m) =>
            m.map((msg) => (msg.id === aId ? { ...msg, content: d.assistant_content! } : msg))
          );
        }
        if (d.stats) {
          setMessages((m) =>
            m.map((msg) => (msg.id === aId ? { ...msg, stats: d.stats } : msg))
          );
        }
      }
      if (event === "error" && data && typeof data === "object") {
        const msg = (data as { message?: string }).message ?? "未知错误";
        setMessages((m) =>
          m.map((s) => (s.id === aId ? { ...s, content: `错误：${msg}`, streaming: false } : s))
        );
      }
    });

    abortRef.current = null;
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
          <div className="sidebar-section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingRight: 6, gap: 4 }}>
            <span>会话</span>
            <div className="session-toolbar-row" onClick={(e) => e.stopPropagation()}>
              <button
                type="button"
                className="purge-sessions-btn"
                disabled={purgingSessions || sessions.length === 0}
                title="一键删除全部会话（含服务器记录）"
                onClick={(e) => void purgeAllSessions(e)}
              >
                {purgingSessions ? "…" : "清空"}
              </button>
              <button
                type="button"
                onClick={() => void syncNow()}
                disabled={syncing}
                title="从服务器同步历史会话"
                className={`sync-btn${syncing ? " spinning" : ""}`}
              >
                ↻
              </button>
            </div>
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
          <div className="field-label" style={{ marginBottom: 6, marginTop: 10 }}>kb_collection</div>
          <input
            className="userid-input"
            aria-label="知识库分区"
            placeholder="留空则用服务端 DEFAULT_KB_COLLECTION"
            value={kbCollection}
            onChange={(e) => {
              setKbCollection(e.target.value);
              saveKbCollection(e.target.value);
            }}
          />
          <div className="field-label" style={{ marginBottom: 4, marginTop: 10 }}>检索文档类型</div>
          <button
            type="button"
            className="btn type-modal-trigger"
            style={{ width: "100%" }}
            onClick={() => setDocTypeModalOpen(true)}
          >
            {activeDocTypes.length === 0
              ? "未限定类型 · 点击在窗口中选择"
              : `已选 ${activeDocTypes.length} 个：${activeDocTypes.join("、")}`}
          </button>
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
          <div className="mode-toggle-group" title="选择对话模式">
            <button
              className={`mode-toggle-btn${chatMode === "rag" ? " active" : ""}`}
              onClick={() => setChatMode("rag")}
              title="普通 RAG 模式：检索知识库后直接回答"
            >
              📚 RAG
            </button>
            <button
              className={`mode-toggle-btn${chatMode === "agent" ? " active" : ""}`}
              onClick={() => setChatMode("agent")}
              title="Agent 模式：LLM 自主决策是否调用工具（ReAct 循环）"
            >
              ⚡ Agent
            </button>
            <button
              className={`mode-toggle-btn${chatMode === "plan" ? " active" : ""}`}
              onClick={() => setChatMode("plan")}
              title="Plan & Execute 模式：先规划子任务，再逐步执行，最后综合生成"
            >
              🗂 规划
            </button>
          </div>
          {currentSession && <span className="badge blue">pgvector</span>}
          <span className="badge green">Ollama · qwen2.5:7b</span>
        </div>

        {health && (
          <div className="health-pop-wrap">
            <pre className="health-pop">{health}</pre>
            <button className="health-pop-close" onClick={() => setHealth(null)} title="关闭">×</button>
          </div>
        )}

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
              disabled={busy}
            />
            {busy
              ? <button className="send-btn stop" onClick={stopGeneration} title="停止生成">■</button>
              : <button className="send-btn" onClick={send} disabled={!input.trim()}>↑</button>
            }
          </div>
        </div>
      </div>

      {docTypeModalOpen && (
        <div
          className="type-modal-root"
          role="dialog"
          aria-modal="true"
          aria-labelledby="chat-doc-type-modal-title"
        >
          <div className="type-modal-backdrop" onClick={() => setDocTypeModalOpen(false)} />
          <div className="type-modal-sheet">
            <header className="type-modal-header">
              <h2 id="chat-doc-type-modal-title" className="type-modal-title">
                配置检索文档类型
              </h2>
              <button
                type="button"
                className="type-modal-close"
                onClick={() => setDocTypeModalOpen(false)}
                aria-label="关闭"
              >
                ×
              </button>
            </header>
            <div className="type-modal-body">
              <div className="doc-type-panel">
                <div className="doc-type-pill-strip" aria-live="polite">
                  {activeDocTypes.length === 0 ? (
                    <span className="doc-type-pill-empty">当前不限定类型（检索全部 doc_type）</span>
                  ) : (
                    activeDocTypes.map((t) => (
                      <button
                        key={t}
                        type="button"
                        className="doc-type-pill"
                        title={`点击移除「${t}」`}
                        onClick={() => removeDocTypeFilter(t)}
                      >
                        <span>{t}</span>
                        <span className="doc-type-pill-x" aria-hidden>
                          ×
                        </span>
                      </button>
                    ))
                  )}
                </div>
                <div className="doc-type-panel-sub">
                  快捷：预设 · 知识库已有
                  {docTypeShortcuts.some((t) => !PRESET_DOC_TYPE_SET.has(t) && !catalogDocTypes.includes(t))
                    ? " · 仅本地保存"
                    : ""}
                </div>
                <div className="doc-type-toolbar">
                  {PRESET_DOC_TYPES.map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`doc-type-chip${activeDocTypes.includes(t) ? " on" : ""}`}
                      onClick={() => toggleDocTypeFilter(t)}
                    >
                      {t}
                    </button>
                  ))}
                  {catalogDocTypes
                    .filter((t) => !PRESET_DOC_TYPE_SET.has(t))
                    .map((t) => (
                      <button
                        key={`cat-${t}`}
                        type="button"
                        className={`doc-type-chip doc-type-chip-catalog${activeDocTypes.includes(t) ? " on" : ""}`}
                        title="当前知识库（或侧栏分区）里已入库文档使用的 doc_type"
                        onClick={() => toggleDocTypeFilter(t)}
                      >
                        {t}
                      </button>
                    ))}
                  {docTypeShortcuts
                    .filter((t) => !PRESET_DOC_TYPE_SET.has(t) && !catalogDocTypes.includes(t))
                    .map((t) => (
                      <button
                        key={`sc-${t}`}
                        type="button"
                        className={`doc-type-chip doc-type-chip-custom${activeDocTypes.includes(t) ? " on" : ""}`}
                        onClick={() => toggleDocTypeFilter(t)}
                        title="本地保存的自定义标签（库中暂无文档时仍可出现）"
                      >
                        {t}
                      </button>
                    ))}
                </div>
                <div className="doc-type-panel-sub">自定义</div>
                <div className="doc-type-add-row">
                  <input
                    className="userid-input"
                    placeholder="输入后添加，如 release-notes"
                    value={customTypeDraft}
                    onChange={(e) => setCustomTypeDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addCustomDocTypeFilter();
                      }
                    }}
                    aria-label="自定义文档类型"
                  />
                  <button type="button" onClick={() => addCustomDocTypeFilter()}>
                    添加
                  </button>
                </div>
                <button
                  type="button"
                  className="doc-type-clear-all"
                  onClick={() => {
                    setActiveDocTypes([]);
                    saveDocTypesForUser(userId, []);
                  }}
                >
                  清空全部类型条件
                </button>
              </div>
            </div>
            <footer className="type-modal-footer">
              <button type="button" className="btn" onClick={() => setDocTypeModalOpen(false)}>
                完成
              </button>
            </footer>
          </div>
        </div>
      )}
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

function formatTime(iso?: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

const MessageRow = memo(function MessageRow({ msg }: { msg: ChatMsg }) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());
  const hasSources = (msg.sources?.length ?? 0) > 0;

  const toggleStep = (i: number) =>
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  const handleCiteClick = (idx: number) => {
    setSourcesOpen(true);
    setHighlightIdx(idx);
    setTimeout(() => setHighlightIdx(null), 2000);
  };

  const copyContent = () => {
    navigator.clipboard.writeText(msg.content).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const timeStr = formatTime(msg.createdAt);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {/* ── Plan & Execute 规划面板 ── */}
      {msg.role === "assistant" && msg.chatMode === "plan" && msg.planGoal && (
        <div className="plan-panel">
          <div className="plan-panel-header">
            <span className="plan-panel-icon">🗂</span>
            <span className="plan-panel-goal">{msg.planGoal}</span>
          </div>
          {(msg.planSteps ?? []).map((step) => (
            <div key={step.id} className={`plan-step-row ${step.uiStatus ?? "pending"}`}>
              <span className="plan-step-num">{step.id}</span>
              <span className="plan-step-desc">{step.description}</span>
              {step.tool && (
                <span className="plan-step-tool">{step.tool}</span>
              )}
              {step.uiStatus === "running" && (
                <span className="agent-step-spinner">···</span>
              )}
              {step.uiStatus === "done" && step.elapsed_ms != null && (
                <span className="agent-step-elapsed">{step.elapsed_ms}ms</span>
              )}
              <span className={`plan-step-dot ${step.uiStatus ?? "pending"}`} />
            </div>
          ))}
        </div>
      )}
      {/* ── Agent / Plan 工具调用步骤面板 ── */}
      {msg.role === "assistant" && msg.chatMode !== "rag" && (msg.agentSteps?.length ?? 0) > 0 && (
        <div className="agent-steps-panel">
          {msg.agentSteps!.map((step, i) => {
            const isExpanded = expandedSteps.has(i);
            // 有内容可展开：done 状态下有 result_summary 或 reasoning
            const hasDetail = step.status === "done" && !!(step.result_summary || step.reasoning);
            // 拼接展开时显示的完整内容块
            const detailLines: { label: string; content: string }[] = [];
            if (step.reasoning) detailLines.push({ label: "推理", content: step.reasoning });
            if (step.args && Object.keys(step.args).length > 0) {
              const firstVal = Object.values(step.args)[0]?.toString() ?? "";
              if (firstVal) detailLines.push({ label: Object.keys(step.args)[0] ?? "参数", content: firstVal });
            }
            if (step.result_summary) detailLines.push({ label: "结果", content: step.result_summary });

            return (
              <div key={i} className="agent-step-wrap">
                <div
                  className={`agent-step-row ${step.status}${hasDetail ? " clickable" : ""}`}
                  onClick={() => hasDetail && toggleStep(i)}
                >
                  <span className="agent-step-icon">{step.icon}</span>
                  <span className="agent-step-label">{step.label}</span>
                  {step.args && Object.keys(step.args).length > 0 && !isExpanded && (
                    <span className="agent-step-args">
                      {Object.values(step.args)[0]?.toString().slice(0, 40)}
                      {(Object.values(step.args)[0]?.toString().length ?? 0) > 40 ? "…" : ""}
                    </span>
                  )}
                  {step.reasoning && !isExpanded && (
                    <span className="agent-step-reasoning" title={step.reasoning}>
                      💭 {step.reasoning.slice(0, 60)}{step.reasoning.length > 60 ? "…" : ""}
                    </span>
                  )}
                  {step.status === "calling" && (
                    <span className="agent-step-spinner">···</span>
                  )}
                  {step.status === "done" && step.source_count != null && step.source_count > 0 && (
                    <span className="agent-step-count">{step.source_count} 个片段</span>
                  )}
                  {step.status === "done" && (step.source_count == null || step.source_count === 0) && step.result_summary && !isExpanded && (
                    <span className="agent-step-result">{step.result_summary.slice(0, 50)}{step.result_summary.length > 50 ? "…" : ""}</span>
                  )}
                  {step.status === "done" && step.elapsed_ms != null && (
                    <span className="agent-step-elapsed">{step.elapsed_ms}ms</span>
                  )}
                  <span className={`agent-step-status-dot ${step.status}`} />
                  {hasDetail && (
                    <span className="agent-step-chevron">{isExpanded ? "▲" : "▼"}</span>
                  )}
                </div>
                {isExpanded && hasDetail && (
                  <div className="agent-step-detail">
                    {detailLines.map((dl, di) => (
                      <div key={di} className="agent-step-detail-item">
                        <span className="agent-step-detail-label">{dl.label}</span>
                        <pre className="agent-step-detail-content">{dl.content}</pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
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
                    {s.score != null && (
                    <span className="source-card-meta" title="混合检索时取向量与文本匹配分的较高值">
                      相关度 {(s.score * 100).toFixed(0)}%
                    </span>
                  )}
                  </div>
                  {s.section_heading?.trim() && (
                    <div className="source-card-section">节：{s.section_heading.trim()}</div>
                  )}
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
          ) : msg.role === "assistant" && msg.streaming ? (
            // 流式阶段：纯文本 + 闪烁光标，避免每 token 触发 ReactMarkdown 重渲染
            <div className="msg-bubble assistant streaming">
              <span className="streaming-text">{msg.content}</span>
              <span className="typing-cursor">▌</span>
            </div>
          ) : (
            <div className={`msg-bubble ${msg.role}`}>
              {msg.role === "assistant"
                ? <MarkdownContent content={msg.content} sources={msg.sources} onCiteClick={handleCiteClick} />
                : msg.content}
            </div>
          )}
          {!msg.streaming && (
            <div className="msg-meta-row">
              {timeStr && <span className="msg-time">{timeStr}</span>}
              {msg.stats && (
                <span className="msg-stats" title={`共 ${msg.stats.tokens} 个 token`}>
                  {msg.stats.tok_per_sec} tok/s
                </span>
              )}
              {msg.role === "assistant" && msg.content && (
                <button className="msg-copy-btn" onClick={copyContent} title="复制回答">
                  {copied ? "✓ 已复制" : "复制"}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
