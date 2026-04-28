"use client";

import { useEffect, useState } from "react";

type Stats = {
  documents: number;
  chunks: number;
  sessions: number;
  messages: number;
  memories: number;
  avg_chunks_per_doc: number;
};

type StatCardProps = { label: string; value: number | string; sub?: string; color?: string };

function StatCard({ label, value, sub, color = "var(--accent)" }: StatCardProps) {
  return (
    <div className="stat-card">
      <div className="stat-value" style={{ color }}>{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export default function StatsPage() {
  const [userId, setUserId] = useState("demo");
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = async (uid: string) => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/stats?user_id=${encodeURIComponent(uid)}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStats(await r.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchStats(userId); }, [userId]);

  return (
    <div className="ingest-layout">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2>系统统计</h2>
        <a href="/" className="btn">← 返回对话</a>
      </div>
      <p className="field-label" style={{ marginBottom: 16 }}>
        当前知识库与用户数据概览，按 user_id 筛选会话/消息/记忆。
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 20, alignItems: "center" }}>
        <span className="field-label" style={{ marginBottom: 0 }}>user_id</span>
        <input
          className="userid-input"
          style={{ width: 160 }}
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          aria-label="用户 ID"
          placeholder="demo"
        />
        <button className="btn" onClick={() => fetchStats(userId)} disabled={loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      {error && <div className="result-block error">❌ {error}（请确认后端已启动）</div>}

      {stats && (
        <>
          <div className="stats-grid">
            <StatCard label="文档总数" value={stats.documents} sub="已入库的原始文件" color="var(--accent)" />
            <StatCard label="向量片段" value={stats.chunks} sub={`平均 ${stats.avg_chunks_per_doc} 片/文档`} color="#79b8ff" />
            <StatCard label="会话数" value={stats.sessions} sub={`用户 ${userId}`} color="var(--green)" />
            <StatCard label="消息总数" value={stats.messages} sub="含用户+助手" color="#4ec9b0" />
            <StatCard label="长期记忆" value={stats.memories} sub="向量化存储" color="#ce9178" />
          </div>

          <div className="stats-bars" style={{ marginTop: 24 }}>
            <div className="field-label" style={{ marginBottom: 8 }}>数据规模分布</div>
            {[
              { label: "文档", value: stats.documents, max: Math.max(stats.documents, 1), color: "var(--accent)" },
              { label: "片段", value: stats.chunks, max: Math.max(stats.chunks, 1), color: "#79b8ff" },
              { label: "消息", value: stats.messages, max: Math.max(stats.messages, 1), color: "var(--green)" },
              { label: "记忆", value: stats.memories, max: Math.max(stats.memories, 1), color: "#ce9178" },
            ].map((item) => {
              const allMax = Math.max(stats.documents, stats.chunks, stats.messages, stats.memories, 1);
              const pct = (item.value / allMax) * 100;
              return (
                <div key={item.label} style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <span style={{ width: 36, fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>{item.label}</span>
                  <div style={{ flex: 1, height: 8, background: "var(--panel)", borderRadius: 4, overflow: "hidden" }}>
                    <div style={{ width: `${pct}%`, height: "100%", background: item.color, borderRadius: 4, transition: "width 0.6s ease" }} />
                  </div>
                  <span style={{ width: 40, fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>{item.value}</span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
