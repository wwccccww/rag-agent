"use client";

import { useCallback, useRef, useState } from "react";

type Result = { type: "success" | "error" | "info"; text: string };

export default function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = (f: File | null) => {
    if (!f) return;
    setFile(f);
    setResult(null);
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  }, []);

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); setOver(true); };
  const onDragLeave = () => setOver(false);

  const submit = async () => {
    if (!file) return;
    setLoading(true);
    setResult({ type: "info", text: "正在向量化并写入 pgvector…" });
    const fd = new FormData();
    fd.append("file", file);
    if (title.trim()) fd.append("title", title.trim());
    try {
      const r = await fetch("/api/ingest", { method: "POST", body: fd });
      const text = await r.text();
      if (r.ok) {
        const data = JSON.parse(text);
        if (data.chunks_created === 0) {
          setResult({ type: "info", text: `该文档已存在（SHA256 相同），跳过重复入库。document_id: ${data.document_id}` });
        } else {
          setResult({ type: "success", text: `✅ 入库成功！共创建 ${data.chunks_created} 个向量片段。\ndocument_id: ${data.document_id}` });
        }
        setFile(null);
        setTitle("");
        if (inputRef.current) inputRef.current.value = "";
      } else {
        setResult({ type: "error", text: `❌ 入库失败 (${r.status})\n${text}` });
      }
    } catch (e) {
      setResult({ type: "error", text: `❌ 请求出错: ${String(e)}` });
    } finally {
      setLoading(false);
    }
  };

  const ext = file?.name.split(".").pop()?.toLowerCase();
  const extOk = !ext || ["txt", "md", "pdf"].includes(ext);

  return (
    <div className="ingest-layout">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2>文档入库</h2>
        <a href="/" className="btn">← 返回对话</a>
      </div>
      <p className="field-label" style={{ marginBottom: 0 }}>
        上传文档，系统将分块并使用 <strong>nomic-embed-text</strong> 向量化写入 Postgres(pgvector)。
        支持 .txt / .md / .pdf。
      </p>

      <div
        className={`drop-zone${over ? " over" : ""}`}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => inputRef.current?.click()}
      >
        <input ref={inputRef} type="file" accept=".txt,.md,.pdf" onChange={(e) => handleFile(e.target.files?.[0] ?? null)} />
        <div className="drop-zone-icon">{file ? "📄" : "☁️"}</div>
        {file ? (
          <>
            <div className="drop-zone-label">{file.name}</div>
            <div className="drop-zone-sub">
              {(file.size / 1024).toFixed(1)} KB
              {!extOk && <span style={{ color: "var(--red)" }}> · 格式不支持</span>}
            </div>
          </>
        ) : (
          <>
            <div className="drop-zone-label">点击或拖拽文件到此处</div>
            <div className="drop-zone-sub">.txt · .md · .pdf</div>
          </>
        )}
      </div>

      <div style={{ marginBottom: 14 }}>
        <div className="field-label">标题（可选）</div>
        <input
          className="text-input"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="例如：产品说明文档"
        />
      </div>

      <button className="btn primary" onClick={submit} disabled={!file || loading || !extOk} style={{ width: "100%", justifyContent: "center", padding: "10px" }}>
        {loading ? "处理中…" : "上传并入库"}
      </button>

      {result && (
        <div className={`result-block ${result.type}`} style={{ whiteSpace: "pre-wrap" }}>
          {result.text}
        </div>
      )}
    </div>
  );
}
