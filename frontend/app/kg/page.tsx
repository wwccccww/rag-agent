"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

function loadUserId(): string {
  try {
    return localStorage.getItem("rag_user_id") || "demo";
  } catch {
    return "demo";
  }
}

type KGEntity = {
  id: string;
  name: string;
  entity_type: string;
  attrs: Record<string, unknown>;
  created_at: string;
};

type KGRelation = {
  id: string;
  subject_id: string;
  subject_name: string;
  predicate: string;
  object_id: string;
  object_name: string;
  confidence: number;
  created_at: string;
};

function prettyName(name: string): string {
  return name === "__self__" ? "我" : name;
}

const TYPE_COLORS: Record<string, string> = {
  person: "#6b8cff",
  project: "#d4a520",
  technology: "#3ec28f",
  organization: "#c76bcf",
  concept: "#8a9aa8",
  event: "#e07c5c",
  other: "#6b7280",
};

function typeColor(t: string): string {
  return TYPE_COLORS[t] ?? TYPE_COLORS.other;
}

/** 简易环形布局：节点 id → { x, y }（SVG 坐标） */
function layoutCircle(ids: string[], w: number, h: number, pad: number): Map<string, { x: number; y: number }> {
  const m = new Map<string, { x: number; y: number }>();
  const n = ids.length;
  if (n === 0) return m;
  const cx = w / 2;
  const cy = h / 2;
  const r = Math.min(w, h) / 2 - pad;
  ids.forEach((id, i) => {
    const ang = (2 * Math.PI * i) / n - Math.PI / 2;
    m.set(id, { x: cx + r * Math.cos(ang), y: cy + r * Math.sin(ang) });
  });
  return m;
}

function KgContent() {
  const params = useSearchParams();
  const [userId, setUserId] = useState(() => params.get("user_id") ?? loadUserId());
  const [entities, setEntities] = useState<KGEntity[]>([]);
  const [relations, setRelations] = useState<KGRelation[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [highlightId, setHighlightId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const q = `user_id=${encodeURIComponent(userId)}&limit=200`;
      const [re, rr] = await Promise.all([
        fetch(`/api/kg/entities?${q}`, { cache: "no-store" }),
        fetch(`/api/kg/relations?${q}`, { cache: "no-store" }),
      ]);
      if (!re.ok) {
        const t = await re.text();
        throw new Error(t || `entities ${re.status}`);
      }
      if (!rr.ok) {
        const t = await rr.text();
        throw new Error(t || `relations ${rr.status}`);
      }
      const ej = (await re.json()) as unknown;
      const rj = (await rr.json()) as unknown;
      setEntities(Array.isArray(ej) ? (ej as KGEntity[]) : []);
      setRelations(Array.isArray(rj) ? (rj as KGRelation[]) : []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setEntities([]);
      setRelations([]);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
  }, [load]);

  const entityMap = useMemo(() => new Map(entities.map((e) => [e.id, e])), [entities]);

  const filteredEntities = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return entities;
    return entities.filter(
      (e) =>
        prettyName(e.name).toLowerCase().includes(f) ||
        e.entity_type.toLowerCase().includes(f),
    );
  }, [entities, filter]);

  const filteredRelations = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return relations;
    return relations.filter(
      (r) =>
        prettyName(r.subject_name).toLowerCase().includes(f) ||
        prettyName(r.object_name).toLowerCase().includes(f) ||
        r.predicate.toLowerCase().includes(f),
    );
  }, [relations, filter]);

  const graphIds = useMemo(() => {
    const s = new Set<string>();
    relations.forEach((r) => {
      s.add(r.subject_id);
      s.add(r.object_id);
    });
    return Array.from(s);
  }, [relations]);

  const svgLayout = useMemo(() => {
    const W = 520;
    const H = 420;
    const pad = 72;
    return { W, H, pos: layoutCircle(graphIds, W, H, pad) };
  }, [graphIds]);

  const removeEntity = async (id: string) => {
    if (!confirm("删除该实体将同时删除其关联关系，确定？")) return;
    const r = await fetch(`/api/kg/entities/${id}?user_id=${encodeURIComponent(userId)}`, {
      method: "DELETE",
    });
    if (!r.ok) {
      setErr(await r.text());
      return;
    }
    await load();
  };

  const tooManyForGraph = graphIds.length > 28;

  return (
    <div className="kg-page ingest-layout">
      <div className="kg-header">
        <h2>知识图谱</h2>
        <div className="kg-header-actions">
          <a href="/memory" className="btn">
            🧠 长期记忆
          </a>
          <a href="/" className="btn">
            ← 返回对话
          </a>
        </div>
      </div>
      <p className="field-label kg-intro">
        展示当前 <code>user_id</code> 下的实体与关系（来自对话自动记忆时的三元组抽取）。数据存于 Postgres{" "}
        <code>kg_entities</code> / <code>kg_relations</code>。
      </p>

      <div className="kg-toolbar">
        <input
          className="text-input"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="user_id"
          aria-label="user_id"
        />
        <button className="btn" type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "加载中…" : "刷新"}
        </button>
        <input
          className="text-input kg-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="筛选名称 / 类型 / 谓词…"
          aria-label="筛选"
        />
      </div>

      {err && (
        <div className="kg-error" role="alert">
          {err}
        </div>
      )}

      <div className="kg-stats">
        <span className="badge">{entities.length} 个实体</span>
        <span className="badge">{relations.length} 条关系</span>
        {tooManyForGraph && (
          <span className="badge">节点 &gt;28，已隐藏环形图；请用下方列表</span>
        )}
      </div>

      {graphIds.length > 0 && !tooManyForGraph && (
        <div className="kg-graph-card">
          <div className="field-label">关系拓扑（点击节点高亮相连边）</div>
          <svg
            className="kg-svg"
            viewBox={`0 0 ${svgLayout.W} ${svgLayout.H}`}
            role="img"
            aria-label="知识图谱拓扑"
          >
            {relations.map((rel) => {
              const a = svgLayout.pos.get(rel.subject_id);
              const b = svgLayout.pos.get(rel.object_id);
              if (!a || !b) return null;
              const hi =
                highlightId &&
                (rel.subject_id === highlightId ||
                  rel.object_id === highlightId ||
                  highlightId === rel.id);
              return (
                <line
                  key={rel.id}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  className={`kg-edge${hi ? " kg-edge-hi" : ""}`}
                />
              );
            })}
            {graphIds.map((id) => {
              const p = svgLayout.pos.get(id);
              const ent = entityMap.get(id);
              if (!p) return null;
              const label = ent?.name ?? id.slice(0, 8);
              const short = label.length > 8 ? `${label.slice(0, 8)}…` : label;
              const col = typeColor(ent?.entity_type ?? "other");
              const hi = highlightId === id;
              return (
                <g key={id} className="kg-node-group">
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={hi ? 22 : 18}
                    fill={col}
                    opacity={hi ? 1 : 0.85}
                    stroke="var(--border, #333)"
                    strokeWidth={1}
                    className="kg-node"
                    onClick={() => setHighlightId((x) => (x === id ? null : id))}
                    style={{ cursor: "pointer" }}
                  />
                  <text
                    x={p.x}
                    y={p.y + 32}
                    textAnchor="middle"
                    className="kg-node-label"
                    fill="var(--text, #ccc)"
                    fontSize={10}
                  >
                    {short}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      <div className="kg-columns">
        <section className="kg-col">
          <h3 className="kg-col-title">实体</h3>
          {filteredEntities.length === 0 && !loading && (
            <p className="field-label">暂无实体。在对话中说「记住，我同事…」等触发记忆写入后会自动抽取。</p>
          )}
          <ul className="kg-entity-list">
            {filteredEntities.map((e) => (
              <li
                key={e.id}
                className={`kg-entity-row${highlightId === e.id ? " kg-row-hi" : ""}`}
                onClick={() => setHighlightId((x) => (x === e.id ? null : e.id))}
              >
                <span className="kg-dot" style={{ background: typeColor(e.entity_type) }} />
                <div className="kg-entity-body">
                  <div className="kg-entity-name">{prettyName(e.name)}</div>
                  <div className="kg-entity-meta">
                    <span className="badge">{e.entity_type}</span>
                    <span className="kg-date">{new Date(e.created_at).toLocaleString("zh-CN")}</span>
                  </div>
                </div>
                <button
                  type="button"
                  className="btn danger kg-del"
                  onClick={(ev) => {
                    ev.stopPropagation();
                    void removeEntity(e.id);
                  }}
                >
                  删
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="kg-col">
          <h3 className="kg-col-title">关系</h3>
          {filteredRelations.length === 0 && !loading && (
            <p className="field-label">暂无关系边。</p>
          )}
          <ul className="kg-relation-list">
            {filteredRelations.map((r) => {
              const hi =
                highlightId &&
                (r.subject_id === highlightId ||
                  r.object_id === highlightId ||
                  highlightId === r.id);
              return (
                <li
                  key={r.id}
                  className={`kg-relation-row${hi ? " kg-row-hi" : ""}`}
                  onClick={() => setHighlightId((x) => (x === r.id ? null : r.id))}
                >
                  <div className="kg-triple">
                    <span className="kg-triple-subj">{prettyName(r.subject_name)}</span>
                    <span className="kg-triple-pred">[{r.predicate}]</span>
                    <span className="kg-triple-obj">{prettyName(r.object_name)}</span>
                  </div>
                  <div className="kg-rel-meta">
                    <span className="badge">{(r.confidence * 100).toFixed(0)}%</span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}

export default function KgPage() {
  return (
    <Suspense fallback={<div className="ingest-layout field-label">加载中…</div>}>
      <KgContent />
    </Suspense>
  );
}
