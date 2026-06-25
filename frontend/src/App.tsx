import { useCallback, useEffect, useRef, useState } from "react";
import { useAgent, useInterrupt, UseAgentUpdate } from "@copilotkit/react-core/v2";

import { AGENT_ID, BACKEND_URL } from "./config";
import { ChatPanel, InterruptForm } from "./components/ChatPanel";
import { FlowPanel } from "./components/FlowPanel";
import { PreviewPanel } from "./components/PreviewPanel";

export type DemoState = {
  spec?: string;
  plan?: string;
  html?: string;
  issues?: string[];
  stage?: string;
  done?: boolean;
  used_fallback?: boolean;
};

export type Choice = { label: string; value: string };

type InterruptPayload = {
  stage?: string;
  type?: string;
  prompt?: string;
  context?: string;
  choices?: Choice[];
  allowText?: boolean;
};

export type Turn = { q: string; a: string };

function parsePayload(raw: unknown): InterruptPayload {
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as InterruptPayload;
    } catch {
      return { prompt: raw };
    }
  }
  return (raw ?? {}) as InterruptPayload;
}

function humanizeError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/fetch|network|failed to fetch|load failed|econnrefused/i.test(msg)) {
    return `Can't reach the backend at ${BACKEND_URL}. Is it running? (try \`python run.py\`)`;
  }
  return msg || "Something went wrong talking to the agent.";
}

// crypto.randomUUID only exists in a secure context (HTTPS/localhost). The app
// can be served over plain HTTP on a LAN, so fall back to a non-crypto id there.
function freshThreadId(): string {
  try {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === "function") return c.randomUUID();
  } catch {
    /* insecure context */
  }
  return `t-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <span>⚠ {message}</span>
      <button onClick={onRetry}>Retry</button>
    </div>
  );
}

export default function App() {
  const { agent } = useAgent({
    agentId: AGENT_ID,
    updates: [
      UseAgentUpdate.OnStateChanged,
      UseAgentUpdate.OnRunStatusChanged,
      UseAgentUpdate.OnMessagesChanged,
    ],
  });

  const state = (agent?.state ?? {}) as DemoState;
  const running = agent?.isRunning ?? false;

  const [transcript, setTranscript] = useState<Turn[]>([]);
  const [runError, setRunError] = useState<string | null>(null);
  const [provider, setProvider] = useState<{ provider?: string; model?: string } | null>(null);
  // Bumped on each (re)start so backend-dependent fetches (the flowchart) recover
  // after the backend comes back up.
  const [reloadSignal, setReloadSignal] = useState(0);

  // Which provider/model is active (for the header badge + diagnosing fallbacks).
  const loadConfig = useCallback(() => {
    fetch(`${BACKEND_URL}/config`)
      .then((r) => r.json())
      .then((d) => setProvider({ provider: d.provider, model: d.models?.generation }))
      .catch(() => {});
  }, []);
  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const interruptEl = useInterrupt({
    agentId: AGENT_ID,
    renderInChat: false,
    render: ({ event, resolve }) => {
      const p = parsePayload(event?.value);
      return (
        <InterruptForm
          prompt={p.prompt ?? "Tell me a little more."}
          context={p.context}
          choices={p.choices}
          allowText={p.allowText ?? true}
          onSubmit={async (value, display) => {
            setTranscript((t) => [...t, { q: p.prompt ?? "", a: display }]);
            try {
              await resolve(value);
            } catch (err) {
              setRunError(humanizeError(err));
            }
          }}
        />
      );
    },
  });

  // Drive the agent. `freshThread: true` rotates to a brand-new thread + clean
  // state (initial load / "New demo"); `false` just re-runs the CURRENT thread
  // (Retry — it re-surfaces wherever the thread is paused, rather than pretending
  // to reset, which the checkpointer would silently ignore).
  const beginRun = useCallback(
    (freshThread: boolean) => {
      if (!agent) return;
      setRunError(null);
      setReloadSignal((s) => s + 1);
      loadConfig();
      // Stop any in-flight stream so a restart can't race it.
      try {
        agent.abortRun();
      } catch {
        /* no active run */
      }
      if (freshThread) {
        agent.threadId = freshThreadId();
        agent.messages = [];
        agent.state = { stage: "elicit", spec: "", html: "", issues: [] } as DemoState;
        setTranscript([]);
      }
      void agent.runAgent().catch((err) => setRunError(humanizeError(err)));
    },
    [agent, loadConfig],
  );

  const started = useRef(false);
  useEffect(() => {
    if (!agent || started.current) return;
    started.current = true;
    beginRun(true);
  }, [agent, beginRun]);

  const newDemo = useCallback(() => beginRun(true), [beginRun]);
  const retry = useCallback(() => beginRun(false), [beginRun]);

  const waiting = interruptEl != null;
  const activeStage = (state.stage === "done" ? "__end__" : state.stage) ?? "elicit";

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          Demo<span>Builder</span>
        </h1>
        <span className="app-sub">build interactive demos by chatting</span>
        {provider?.provider ? (
          <span className="provider-badge" title="Active LLM provider for generation">
            via {provider.provider}
            {provider.model ? ` · ${provider.model}` : ""}
          </span>
        ) : null}
        <button
          className="new-demo-btn"
          onClick={newDemo}
          disabled={running}
          title={running ? "Wait for the current step to finish" : "Start a new demo"}
        >
          ↻ New demo
        </button>
      </header>

      {runError ? <ErrorBanner message={runError} onRetry={retry} /> : null}

      <main className="panels">
        <section className="panel">
          <h2 className="panel-title">1 · Chat</h2>
          <ChatPanel
            transcript={transcript}
            interruptEl={interruptEl}
            running={running}
            waiting={waiting}
            done={state.done ?? false}
          />
        </section>

        <section className="panel">
          <h2 className="panel-title">2 · Workflow</h2>
          <FlowPanel activeStage={activeStage} waiting={waiting} reloadSignal={reloadSignal} />
        </section>

        <section className="panel">
          <h2 className="panel-title">3 · Preview</h2>
          <PreviewPanel
            html={state.html}
            done={state.done ?? false}
            spec={state.spec}
            fallback={state.used_fallback ?? false}
          />
        </section>
      </main>
    </div>
  );
}
