"use client";

import { useState } from "react";

export default function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [log, setLog] = useState<string>("");

  async function submit() {
    setLog("");
    const fd = new FormData();
    if (file) fd.append("file", file);
    if (title.trim()) fd.append("title", title.trim());
    const r = await fetch("/api/ingest", { method: "POST", body: fd });
    const t = await r.text();
    setLog(`${r.status}\n${t}`);
  }

  return (
    <div className="container">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>文档入库</h2>
        <a href="/">返回对话</a>
      </div>
      <p className="muted">上传 .txt / .md / .pdf，将向量化写入 Postgres(pgvector)。</p>

      <div className="card" style={{ marginTop: 12 }}>
        <div className="row" style={{ marginBottom: 10 }}>
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div style={{ marginBottom: 10 }}>
          <div className="muted" style={{ marginBottom: 6 }}>
            标题（可选）
          </div>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：产品说明" />
        </div>
        <button onClick={submit} disabled={!file}>
          上传并入库
        </button>
      </div>

      {log ? (
        <pre className="card" style={{ marginTop: 12, whiteSpace: "pre-wrap" }}>
          {log}
        </pre>
      ) : null}
    </div>
  );
}
