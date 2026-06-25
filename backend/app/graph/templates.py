"""Deterministic fallbacks used when no LLM provider is reachable.

These keep the full 7-stage loop runnable (and demoable) without Ollama/Azure.
They are clearly labeled as placeholders; Stage 3 replaces them with real model
output via app/llm/complete.py.
"""

from __future__ import annotations

import re
from html import escape

# A complete, self-contained interactive demo (canvas + speed slider). Inline
# CSS/JS, no external requests. __SPEC__ / __PLAN__ are replaced with escaped text.
_STUB_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>DemoBuilder demo</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }
  .wrap { max-width: 680px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 1.15rem; margin: 0 0 4px; }
  .spec { color: #94a3b8; font-size: .9rem; margin: 0 0 6px; }
  .plan { color: #cbd5e1; font-size: .82rem; white-space: pre-wrap; background:#1e293b;
          border-radius:10px; padding:10px 12px; margin: 0 0 16px; }
  canvas { width: 100%; height: 260px; background: #1e293b; border-radius: 12px; display: block; }
  .controls { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
  label { font-size: .85rem; color: #cbd5e1; }
  input[type=range] { flex: 1; accent-color: #ff5c00; }
  .badge { display:inline-block; margin-top:18px; padding:4px 10px; border-radius:999px;
           background:#ff5c0022; color:#ff9d66; font-size:.72rem; letter-spacing:.04em; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Placeholder demo</h1>
  <p class="spec">You asked for: <em>&ldquo;__SPEC__&rdquo;</em></p>
  <div class="plan">__PLAN__</div>
  <canvas id="c" width="600" height="260"></canvas>
  <div class="controls">
    <label for="speed">Speed</label>
    <input id="speed" type="range" min="0" max="12" value="4" />
    <span id="readout">4</span>
  </div>
  <span class="badge">PLACEHOLDER · real generation runs once an LLM is configured (Stage&nbsp;3)</span>
</div>
<script>
  const canvas = document.getElementById('c');
  const ctx = canvas.getContext('2d');
  const speed = document.getElementById('speed');
  const readout = document.getElementById('readout');
  let x = 80, y = 80, vx = 4, vy = 3;
  function step() {
    const s = Number(speed.value);
    const w = canvas.width, h = canvas.height, r = 16;
    x += (vx > 0 ? 1 : -1) * s * 0.5;
    y += (vy > 0 ? 1 : -1) * s * 0.4;
    if (x < r || x > w - r) vx = -vx;
    if (y < r || y > h - r) vy = -vy;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = '#ff5c00';
    ctx.fill();
    requestAnimationFrame(step);
  }
  speed.addEventListener('input', () => { readout.textContent = speed.value; });
  step();
</script>
</body>
</html>
"""

# Tolerate attributes on the opening tag (the emitted banner has a style="…").
_FIX_BANNER_RE = re.compile(r"<div data-fixbanner\b[^>]*>.*?</div>", re.DOTALL)


def fallback_plan(spec: str) -> str:
    s = spec.strip() or "an interactive demo"
    return (
        f'Here\'s my plan for "{s}":\n'
        "• A single self-contained HTML page that opens in any browser\n"
        "• An interactive visual area (canvas) illustrating the idea\n"
        "• Controls (a slider/buttons) to explore it\n"
        "• A short on-screen instruction so it's self-explanatory\n"
        "(Placeholder plan — real AI planning turns on once a model is configured.)"
    )


def fallback_html(spec: str, plan: str) -> str:
    safe_spec = escape((spec or "an interactive demo").strip()) or "an interactive demo"
    safe_plan = escape((plan or "").strip())
    return _STUB_TEMPLATE.replace("__SPEC__", safe_spec).replace("__PLAN__", safe_plan)


def fallback_fix(html: str, issue: str) -> str:
    """Visibly note the requested change so the Fix → Review loop is demoable."""
    note = escape(issue.strip() or "your change")
    banner = (
        '<div data-fixbanner style="position:fixed;left:0;right:0;bottom:0;'
        "background:#ff5c00;color:#fff;font:13px/1.4 system-ui;padding:8px 12px;"
        'z-index:99999">Requested change noted: <b>'
        f"{note}</b> <span style=\"opacity:.85\">(placeholder — real edits apply "
        'once a model is configured)</span></div>'
    )
    base = _FIX_BANNER_RE.sub("", html or fallback_html("", ""))
    if "</body>" in base:
        return base.replace("</body>", banner + "</body>", 1)
    return base + banner
