import { useState } from "react";

export default function DownloadForm({
  url,
  setUrl,
  outputPath,
  setOutputPath,
  onAnalyze,
  onDownload,
  analyzing,
  downloading,
  analysis
}) {
  const [pasteError, setPasteError] = useState("");

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      setUrl(text);
      setPasteError("");
    } catch (err) {
      setPasteError("Clipboard read failed.");
    }
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">Download</h2>
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-zinc-400">File URL</label>
          <div className="flex gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="https://example.com/file.zip"
            />
            <button
              onClick={handlePaste}
              type="button"
              className="rounded-md border border-zinc-700 px-3 text-xs hover:border-accent"
            >
              Paste
            </button>
          </div>
          {pasteError && <p className="mt-1 text-xs text-red-400">{pasteError}</p>}
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Output path</label>
          <input
            value={outputPath}
            onChange={(e) => setOutputPath(e.target.value)}
            className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-accent"
            placeholder="C:\\Downloads\\movie.iso"
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={onAnalyze}
            disabled={analyzing}
            className="rounded-md bg-accent px-4 py-2 text-xs font-semibold text-white disabled:opacity-60"
          >
            {analyzing ? "Analyzing..." : "Analyze"}
          </button>
          <button
            onClick={onDownload}
            disabled={downloading}
            className="rounded-md bg-success px-4 py-2 text-xs font-semibold text-black disabled:opacity-60"
          >
            {downloading ? "Downloading..." : "Download"}
          </button>
        </div>

        {analysis && (
          <div className="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-300">
            <p>
              Byte ranges:{" "}
              <span className={analysis.supports_ranges ? "text-success" : "text-red-400"}>
                {analysis.supports_ranges ? "Supported" : "Not supported"}
              </span>
            </p>
            <p>File size: {(analysis.content_length / (1024 * 1024)).toFixed(2)} MB</p>
            <p>Content type: {analysis.content_type}</p>
          </div>
        )}
      </div>
    </div>
  );
}
