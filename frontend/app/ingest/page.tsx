"use client";

import { useCallback, useRef, useState } from "react";

type Result = { type: "success" | "error" | "info"; text: string };
type Mode = "file" | "url" | "text";

export default function IngestPage() {
  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [urlInput, setUrlInput] = useState("");
  const [pasteText, setPasteText] = useState("");
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

  const canSubmit = () => {
    if (loading) return false;
    if (mode === "file") return !!file && extOk;
    if (mode === "url") return urlInput.trim().startsWith("http");
    if (mode === "text") return pasteText.trim().length > 0;
    return false;
  };

  const submit = async () => {
    if (!canSubmit()) return;
    setLoading(true);
    setResult({ type: "info", text: mode === "url" ? "正在抓取网页并向量化…" : "正在向量化并写入 pgvector…" });

    const fd = new FormData();
    if (mode === "file" && file) {
      fd.append("file", file);
    } else if (mode === "url") {
      fd.append("url", urlInput.trim());
    } else if (mode === "text") {
      fd.append("text", pasteText.trim());
    }
    if (title.trim()) fd.append("title", title.trim());

    try {
      const r = await fetch("/api/ingest", { method: "POST", body: fd });
      const txt = await r.text();
      if (r.ok) {
        const data = JSON.parse(txt);
        if (data.chunks_created === 0) {
          setResult({ type: "info", text: `该内容已存在（SHA256 相同），跳过重复入库。\ndocument_id: ${data.document_id}` });
        } else {
          setResult({ type: "success", text: `✅ 入库成功！共创建 ${data.chunks_created} 个向量片段。\ndocument_id: ${data.document_id}` });
        }
        setFile(null);
        setUrlInput("");
        setPasteText("");
        setTitle("");
        if (inputRef.current) inputRef.current.value = "";
      } else {
        setResult({ type: "error", text: `❌ 入库失败 (${r.status})\n${txt}` });
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
      <p className="field-label" style={{ marginBottom: 12 }}>
        分块 → <strong>nomic-embed-text</strong> 向量化 → 写入 Postgres(pgvector)。
      </p>

      {/* 模式切换 Tab */}
      <div className="ingest-tabs">
        {(["file", "url", "text"] as Mode[]).map((m) => (
          <button
            key={m}
            className={`ingest-tab${mode === m ? " active" : ""}`}
            onClick={() => { setMode(m); setResult(null); }}
          >
            {m === "file" ? "📄 上传文件" : m === "url" ? "🌐 网页 URL" : "📝 粘贴文本"}
          </button>
        ))}
      </div>

      {/* 文件上传 */}
      {mode === "file" && (
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
      )}

      {/* URL 输入 */}
      {mode === "url" && (
        <div style={{ marginBottom: 14 }}>
          <div className="field-label">网页地址</div>
          <input
            className="text-input"
            type="url"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="https://example.com/article"
            aria-label="网页 URL"
          />
          <div className="field-label" style={{ marginTop: 6, color: "var(--text-dim)" }}>
            后端会自动抓取正文，过滤导航栏/页脚等无效内容
          </div>
        </div>
      )}

      {/* 粘贴文本 */}
      {mode === "text" && (
        <div style={{ marginBottom: 14 }}>
          <div className="field-label">粘贴文本内容</div>
          <textarea
            className="text-input"
            rows={8}
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder="在此粘贴文本内容…"
            aria-label="粘贴文本"
            style={{ resize: "vertical", fontFamily: "inherit" }}
          />
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <div className="field-label">自定义标题（可选）</div>
        <input
          className="text-input"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="例如：产品说明文档"
          aria-label="文档标题"
        />
      </div>

      <button
        className="btn primary"
        onClick={submit}
        disabled={!canSubmit()}
        style={{ width: "100%", justifyContent: "center", padding: "10px" }}
      >
        {loading ? "处理中…" : mode === "url" ? "抓取并入库" : "上传并入库"}
      </button>

      {result && (
        <div className={`result-block ${result.type}`} style={{ whiteSpace: "pre-wrap" }}>
          {result.text}
        </div>
      )}
    </div>
  );
}
