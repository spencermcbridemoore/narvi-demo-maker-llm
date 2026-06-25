function slug(text: string | undefined, fallback = "demo"): string {
  const s = (text || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return s.slice(0, 40) || fallback;
}

function downloadHtml(html: string, name: string) {
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function PreviewPanel({
  html,
  done,
  spec,
  fallback,
}: {
  html?: string;
  done?: boolean;
  spec?: string;
  fallback?: boolean;
}) {
  const ready = Boolean(html && html.trim());

  return (
    <div className="preview">
      <div className="preview-bar">
        <span className="preview-hint">{ready ? "Interactive — try it" : "Waiting for your demo"}</span>
        <button
          className={`download-btn${done ? " ready" : ""}`}
          disabled={!ready}
          onClick={() => ready && html && downloadHtml(html, `${slug(spec)}.html`)}
        >
          ⤓ Download .html
        </button>
      </div>

      {ready && fallback ? (
        <div className="preview-fallback" role="status">
          ⚠ This is a built-in <b>placeholder</b> — no LLM provider was reachable. Check your
          provider config (the header shows the active one).
        </div>
      ) : null}

      {ready ? (
        // Sandboxed: "allow-scripts" WITHOUT "allow-same-origin" → opaque origin.
        // Its JS (incl. CDN scripts) runs but it can't touch this page.
        <iframe
          title="demo-preview"
          className="preview-frame"
          srcDoc={html}
          sandbox="allow-scripts"
        />
      ) : (
        <div className="preview-empty">
          Your interactive demo will render here once it&apos;s generated.
        </div>
      )}
    </div>
  );
}
