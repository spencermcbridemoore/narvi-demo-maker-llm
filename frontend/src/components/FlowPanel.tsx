import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

import { BACKEND_URL } from "../config";

mermaid.initialize({
  startOnLoad: false,
  // The diagram source comes from OUR backend (graph.get_graph().draw_mermaid()),
  // not user input, so "loose" is safe and lets the HTML node labels render.
  securityLevel: "loose",
  theme: "dark",
  flowchart: { curve: "linear" },
});

// Mermaid renders each node as <g class="node" id="<renderId>-flowchart-<nodeId>-<n>">.
// Extract <nodeId> and toggle a highlight class on the node matching the active stage.
function applyHighlight(container: HTMLElement, target: string) {
  container.querySelectorAll("g.node").forEach((node) => {
    const match = node.id.match(/flowchart-(.+)-\d+$/);
    const nodeId = match ? match[1] : node.id;
    node.classList.toggle("node--active", nodeId === target);
  });
}

export function FlowPanel({
  activeStage,
  waiting,
  reloadSignal = 0,
}: {
  activeStage: string;
  waiting: boolean;
  reloadSignal?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch the REAL graph topology; re-fetch when reloadSignal bumps so the
  // flowchart recovers if the backend was down on first load.
  useEffect(() => {
    let cancelled = false;
    fetch(`${BACKEND_URL}/mermaid`)
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) {
          setSource(d.mermaid as string);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadSignal]);

  // Render the SVG when the topology arrives (not on every highlight change).
  useEffect(() => {
    if (!source || !ref.current) return;
    let cancelled = false;
    void mermaid.render("demobuilder-graph", source).then(({ svg }) => {
      if (cancelled || !ref.current) return;
      ref.current.innerHTML = svg;
      applyHighlight(ref.current, activeStage);
    });
    return () => {
      cancelled = true;
    };
  }, [source]); // activeStage handled by the effect below

  // Re-apply the "you are here" highlight whenever the active stage changes.
  useEffect(() => {
    if (ref.current?.querySelector("svg")) applyHighlight(ref.current, activeStage);
  }, [activeStage]);

  if (error) {
    return <div className="flow-error">Couldn&apos;t load the workflow graph.<br />{error}</div>;
  }

  return (
    <div className="flow-wrap">
      <div className={`flow${waiting ? " flow--waiting" : ""}`} ref={ref} />
      <p className="flow-legend">
        {waiting ? (
          <>
            <span className="legend-dot waiting" /> waiting on you
          </>
        ) : (
          <>
            <span className="legend-dot" /> current stage
          </>
        )}
      </p>
    </div>
  );
}
