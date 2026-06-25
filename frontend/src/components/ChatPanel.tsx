import { Fragment, useState } from "react";
import type { FormEvent, ReactElement } from "react";

import type { Choice, Turn } from "../App";

export function InterruptForm({
  prompt,
  context,
  choices,
  allowText = true,
  onSubmit,
}: {
  prompt: string;
  context?: string;
  choices?: Choice[];
  allowText?: boolean;
  onSubmit: (value: string, display: string) => unknown | Promise<unknown>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async (value: string, display: string) => {
    if (busy || !value.trim()) return;
    setBusy(true);
    try {
      await onSubmit(value, display);
      setText("");
    } finally {
      setBusy(false);
    }
  };

  const submitText = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (trimmed) void send(trimmed, trimmed);
  };

  return (
    <form className="interrupt" onSubmit={submitText}>
      {context ? <div className="interrupt-context">{context}</div> : null}
      <p className="interrupt-prompt">{prompt}</p>

      {choices && choices.length > 0 ? (
        <div className="interrupt-choices">
          {choices.map((c) => (
            <button
              key={c.value}
              type="button"
              className="choice-btn"
              disabled={busy}
              onClick={() => void send(c.value, c.label)}
            >
              {c.label}
            </button>
          ))}
        </div>
      ) : null}

      {allowText ? (
        <div className="interrupt-textrow">
          <textarea
            className="interrupt-input"
            rows={2}
            value={text}
            autoFocus
            disabled={busy}
            placeholder={choices?.length ? "…or type a response" : "Type your answer…"}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="interrupt-send" type="submit" disabled={busy || !text.trim()}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      ) : null}
    </form>
  );
}

export function ChatPanel({
  transcript,
  interruptEl,
  running,
  waiting,
  done,
}: {
  transcript: Turn[];
  interruptEl: ReactElement | null;
  running: boolean;
  waiting: boolean;
  done: boolean;
}) {
  return (
    <div className="chat">
      <div className="bubble bot">
        Hi! I&apos;m DemoBuilder. Tell me what interactive demo you&apos;d like to build and
        I&apos;ll create a live preview you can try — then we&apos;ll refine it together.
      </div>

      {transcript.map((turn, i) => (
        <Fragment key={i}>
          {turn.q ? <div className="bubble bot">{turn.q}</div> : null}
          <div className="bubble user">{turn.a}</div>
        </Fragment>
      ))}

      {interruptEl ? <div className="chat-interrupt">{interruptEl}</div> : null}

      {running && !waiting ? (
        <div className="chat-status">
          <span className="dot" /> Working on it…
        </div>
      ) : null}

      {done ? (
        <div className="bubble bot">
          🎉 All set! Download your finished demo from the Preview panel — it&apos;s a single
          self-contained <code>.html</code> file you can open, host, or email.
        </div>
      ) : null}
    </div>
  );
}
