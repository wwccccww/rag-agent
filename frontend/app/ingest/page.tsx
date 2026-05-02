"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Mode = "file" | "url" | "text";
type FileStatus = "pending" | "uploading" | "success" | "dup" | "error";
type FileEntry = { file: File; status: FileStatus; message?: string };
type SingleResult = { type: "success" | "error" | "info"; text: string };

const ALLOWED_EXTS = ["txt", "md", "pdf", "docx", "xlsx"];
const MAX_MB = 50;
const KB_COLLECTION_KEY = "rag_kb_collection";

function loadKbCollection(): string {
  try {
    return localStorage.getItem(KB_COLLECTION_KEY) ?? "";
  } catch {
    return "";
  }
}

function getExt(name: string) { return name.split(".").pop()?.toLowerCase() ?? ""; }
function isExtOk(name: string) { return ALLOWED_EXTS.includes(getExt(name)); }
function fmtSize(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${(bytes / 1024).toFixed(1)} KB`;
}

const STATUS_ICON: Record<FileStatus, string> = {
  pending: "⏳",
  uploading: "⬆️",
  success: "✅",
  dup: "⚠️",
  error: "❌",
};

export default function IngestPage() {
  const [mode, setMode] = useState<Mode>("file");
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [singleResult, setSingleResult] = useState<SingleResult | null>(null);
  const [over, setOver] = useState(false);
  const [kbCollection, setKbCollection] = useState("");
  const [docType, setDocType] = useState("general");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setKbCollection(loadKbCollection());
  }, []);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const arr = Array.from(newFiles);
    setFiles((prev) => {
      const existingNames = new Set(prev.map((e) => e.file.name));
      const added = arr
        .filter((f) => !existingNames.has(f.name))
        .map((f) => ({ file: f, status: "pending" as FileStatus }));
      return [...prev, ...added];
    });
    setSingleResult(null);
  }, []);

  const removeFile = (idx: number) => setFiles((prev) => prev.filter((_, i) => i !== idx));

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  }, [addFiles]);

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); setOver(true); };
  const onDragLeave = () => setOver(false);

  const canSubmit = () => {
    if (loading) return false;
    if (mode === "file") return files.length > 0 && files.some((e) => isExtOk(e.file.name) && e.file.size <= MAX_MB * 1024 * 1024);
    if (mode === "url") return urlInput.trim().startsWith("http");
    if (mode === "text") return pasteText.trim().length > 0;
    return false;
  };

  const submitFiles = async () => {
    setLoading(true);
    for (let i = 0; i < files.length; i++) {
      const entry = files[i];
      if (!isExtOk(entry.file.name)) {
        setFiles((prev) => prev.map((e, idx) => idx === i ? { ...e, status: "error", message: "格式不支持" } : e));
        continue;
      }
      if (entry.file.size > MAX_MB * 1024 * 1024) {
        setFiles((prev) => prev.map((e, idx) => idx === i ? { ...e, status: "error", message: `超过 ${MAX_MB} MB 限制` } : e));
        continue;
      }
      setFiles((prev) => prev.map((e, idx) => idx === i ? { ...e, status: "uploading" } : e));
      try {
        const fd = new FormData();
        fd.append("file", entry.file);
        if (title.trim()) fd.append("title", title.trim());
        if (kbCollection.trim()) fd.append("kb_collection", kbCollection.trim());
        fd.append("doc_type", docType);
        const r = await fetch("/api/ingest", { method: "POST", body: fd });
        const txt = await r.text();
        if (r.ok) {
          const data = JSON.parse(txt);
          const isNew = data.chunks_created > 0;
          setFiles((prev) => prev.map((e, idx) => idx === i ? {
            ...e,
            status: isNew ? "success" : "dup",
            message: isNew ? `创建 ${data.chunks_created} 个片段` : "内容已存在，跳过",
          } : e));
        } else {
          setFiles((prev) => prev.map((e, idx) => idx === i ? { ...e, status: "error", message: txt.slice(0, 120) } : e));
        }
      } catch (e) {
        setFiles((prev) => prev.map((e2, idx) => idx === i ? { ...e2, status: "error", message: String(e) } : e2));
      }
    }
    setLoading(false);
  };

  const submitSingle = async () => {
    setLoading(true);
    setSingleResult({ type: "info", text: mode === "url" ? "正在抓取网页并向量化…" : "正在向量化并写入 pgvector…" });
    const fd = new FormData();
    if (mode === "url") fd.append("url", urlInput.trim());
    else fd.append("text", pasteText.trim());
    if (title.trim()) fd.append("title", title.trim());
    if (kbCollection.trim()) fd.append("kb_collection", kbCollection.trim());
    fd.append("doc_type", docType);
    try {
      const r = await fetch("/api/ingest", { method: "POST", body: fd });
      const txt = await r.text();
      if (r.ok) {
        const data = JSON.parse(txt);
        if (data.chunks_created === 0) {
          setSingleResult({ type: "info", text: `内容已存在（SHA256 相同），跳过。\ndocument_id: ${data.document_id}` });
        } else {
          setSingleResult({ type: "success", text: `✅ 入库成功！共创建 ${data.chunks_created} 个向量片段。\ndocument_id: ${data.document_id}` });
          setUrlInput(""); setPasteText(""); setTitle("");
        }
      } else {
        setSingleResult({ type: "error", text: `❌ 入库失败 (${r.status})\n${txt}` });
      }
    } catch (e) {
      setSingleResult({ type: "error", text: `❌ 请求出错: ${String(e)}` });
    } finally {
      setLoading(false);
    }
  };

  const submit = () => (mode === "file" ? submitFiles() : submitSingle());

  const clearDone = () => setFiles((prev) => prev.filter((e) => e.status === "pending" || e.status === "uploading"));

  const doneCount = files.filter((e) => e.status === "success" || e.status === "dup" || e.status === "error").length;

  return (
    <div className="ingest-layout">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2>文档入库</h2>
        <a href="/" className="btn">← 返回对话</a>
      </div>
      <p className="field-label" style={{ marginBottom: 12 }}>
        分块 → <strong>nomic-embed-text</strong> 向量化 → 写入 Postgres(pgvector)。
      </p>
      <div style={{ marginBottom: 14, display: "flex", flexDirection: "column", gap: 8 }}>
        <div>
          <div className="field-label">kb_collection（与对话页共用 localStorage）</div>
          <input
            className="text-input"
            value={kbCollection}
            onChange={(e) => {
              const v = e.target.value;
              setKbCollection(v);
              try {
                localStorage.setItem(KB_COLLECTION_KEY, v);
              } catch {}
            }}
            placeholder="留空则 default"
            aria-label="知识库分区"
          />
        </div>
        <div>
          <div className="field-label">doc_type</div>
          <select
            className="text-input"
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
            aria-label="文档类型"
          >
            <option value="general">general（其他）</option>
            <option value="tutorial">tutorial（教程）</option>
            <option value="api">api（接口说明）</option>
            <option value="requirements">requirements（需求）</option>
          </select>
        </div>
      </div>

      {/* 模式切换 */}
      <div className="ingest-tabs">
        {(["file", "url", "text"] as Mode[]).map((m) => (
          <button
            key={m}
            className={`ingest-tab${mode === m ? " active" : ""}`}
            onClick={() => { setMode(m); setSingleResult(null); }}
          >
            {m === "file" ? "📄 上传文件" : m === "url" ? "🌐 网页 URL" : "📝 粘贴文本"}
          </button>
        ))}
      </div>

      {/* ── 文件上传（批量）── */}
      {mode === "file" && (
        <>
          <div
            className={`drop-zone${over ? " over" : ""}`}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onClick={() => inputRef.current?.click()}
          >
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".txt,.md,.pdf,.docx,.xlsx"
              aria-label="选择文件（支持多选）"
              onChange={(e) => e.target.files && addFiles(e.target.files)}
            />
            <div className="drop-zone-icon">{files.length > 0 ? "📂" : "☁️"}</div>
            {files.length > 0 ? (
              <>
                <div className="drop-zone-label">已选 {files.length} 个文件（点击继续添加）</div>
                <div className="drop-zone-sub">.txt · .md · .pdf · .docx · .xlsx，单文件最大 {MAX_MB} MB</div>
              </>
            ) : (
              <>
                <div className="drop-zone-label">点击或拖拽文件到此处（支持多选）</div>
                <div className="drop-zone-sub">.txt · .md · .pdf · .docx · .xlsx，单文件最大 {MAX_MB} MB</div>
              </>
            )}
          </div>

          {files.length > 0 && (
            <div className="file-list">
              {files.map((entry, i) => {
                const extOk = isExtOk(entry.file.name);
                const tooBig = entry.file.size > MAX_MB * 1024 * 1024;
                const warn = !extOk ? "格式不支持" : tooBig ? `超过 ${MAX_MB} MB` : null;
                return (
                  <div key={i} className={`file-list-item status-${entry.status}`}>
                    <span className="file-list-icon">{STATUS_ICON[entry.status]}</span>
                    <div className="file-list-info">
                      <span className="file-list-name">{entry.file.name}</span>
                      <span className="file-list-meta">
                        {fmtSize(entry.file.size)}
                        {warn && <span className="file-list-warn"> · {warn}</span>}
                        {entry.message && <span className="file-list-msg"> · {entry.message}</span>}
                      </span>
                    </div>
                    {entry.status === "pending" && (
                      <button className="file-list-remove" onClick={() => removeFile(i)} title="移除">✕</button>
                    )}
                  </div>
                );
              })}
              {doneCount > 0 && (
                <button className="btn" style={{ marginTop: 6, fontSize: 11 }} onClick={clearDone}>
                  清除已完成 ({doneCount})
                </button>
              )}
            </div>
          )}
        </>
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

      {mode !== "file" && (
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
      )}

      <button
        className="btn primary"
        onClick={submit}
        disabled={!canSubmit()}
        style={{ width: "100%", justifyContent: "center", padding: "10px" }}
      >
        {loading
          ? "处理中…"
          : mode === "file"
            ? files.length > 1 ? `上传 ${files.filter((e) => e.status === "pending").length} 个文件` : "上传并入库"
            : mode === "url" ? "抓取并入库" : "上传并入库"}
      </button>

      {singleResult && (
        <div className={`result-block ${singleResult.type}`} style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>
          {singleResult.text}
        </div>
      )}
    </div>
  );
}
