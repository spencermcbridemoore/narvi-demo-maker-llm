// A step's navigation column: a sticky list of its sections on the left, with the
// section on screen highlighted. On a narrow screen it is a "Jump to" menu instead.
import { useEffect, useRef, useState } from "react";

/** A place to jump to: the element marked ``data-nav={key}``. */
export type NavEntry = { key: string; label: string; mark?: "own" | "needed" };
export type NavGroup = { title: string; entries: NavEntry[] };

/**
 * The line a section's top has to cross to count as the one being looked at: just below
 * where a jump puts it (its scroll-margin-top), so a short section reached by a jump is
 * not taken over by the one after it. Lower on narrow screens, under the "Jump to" bar.
 */
const lineFor = (width: number) => (width <= 1000 ? 70 : 24);

const target = (key: string) => document.querySelector<HTMLElement>(`[data-nav="${CSS.escape(key)}"]`);

/** The entry on screen now: the last marked section whose top has crossed the line. */
export function currentOf(
  keys: string[],
  tops: (key: string) => number | null,
  atBottom: boolean,
  line = 24,
): string | null {
  if (!keys.length) return null;
  if (atBottom) return keys[keys.length - 1];
  let found = keys[0];
  for (const key of keys) {
    const top = tops(key);
    if (top !== null && top <= line) found = key;
  }
  return found;
}

/** The current section, and a way to pin the one a jump went to until the reader scrolls. */
function useCurrentSection(keys: string[]): [string | null, (key: string) => void] {
  const [current, setCurrent] = useState<string | null>(keys[0] ?? null);
  const pinned = useRef<string | null>(null);
  const joined = keys.join("\u0000");
  useEffect(() => {
    const list = joined ? joined.split("\u0000") : [];
    let frame = 0;
    const measure = () => {
      frame = 0;
      // A section a jump went to stays current (the last ones can never reach the top)
      // until the reader scrolls or clicks on the page themselves.
      if (pinned.current && target(pinned.current)) return setCurrent(pinned.current);
      pinned.current = null;
      const atBottom = window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      setCurrent(currentOf(list, (k) => target(k)?.getBoundingClientRect().top ?? null, atBottom, lineFor(window.innerWidth)));
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    const byHand = () => {
      pinned.current = null;
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    window.addEventListener("wheel", byHand, { passive: true });
    window.addEventListener("touchmove", byHand, { passive: true });
    window.addEventListener("keydown", byHand);
    window.addEventListener("mousedown", byHand); // dragging the scrollbar starts here
    return () => {
      window.removeEventListener("mousedown", byHand);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("wheel", byHand);
      window.removeEventListener("touchmove", byHand);
      window.removeEventListener("keydown", byHand);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [joined]);
  const pin = (key: string) => {
    pinned.current = key;
    setCurrent(key);
  };
  return [current, pin];
}

export function SectionNav({ groups, label = "On this page" }: { groups: NavGroup[]; label?: string }) {
  const keys = groups.flatMap((g) => g.entries.map((e) => e.key));
  const [current, pin] = useCurrentSection(keys);
  const [filter, setFilter] = useState<Record<string, string>>({});
  const list = useRef<HTMLElement>(null);
  const go = (key: string) => {
    const el = target(key);
    if (!el) return;
    pin(key);
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // Keep the highlighted entry in view when the list itself scrolls (many versions).
  useEffect(() => {
    if (!current || !list.current) return;
    const entry = list.current.querySelector<HTMLElement>(`[data-entry="${CSS.escape(current)}"]`);
    if (!entry) return;
    const box = list.current.getBoundingClientRect();
    const at = entry.getBoundingClientRect();
    if (at.top < box.top || at.bottom > box.bottom) list.current.scrollTop += at.top - box.top - box.height / 3;
  }, [current]);

  return (
    <>
      <nav className="g-nav" aria-label={label} ref={list}>
        {groups.map((g) => {
          const q = (filter[g.title] ?? "").trim().toLowerCase();
          const shown = q ? g.entries.filter((e) => e.label.toLowerCase().includes(q)) : g.entries;
          return (
            <div className="g-nav-group" key={g.title}>
              <div className="g-nav-title">
                {g.title}
                {g.entries.length > 1 && <span className="g-nav-count"> · {g.entries.length}</span>}
              </div>
              {g.entries.length > 12 && (
                <input
                  className="g-nav-filter"
                  type="search"
                  placeholder="Find…"
                  aria-label={`Find in ${g.title}`}
                  value={filter[g.title] ?? ""}
                  onChange={(e) => setFilter((f) => ({ ...f, [g.title]: e.target.value }))}
                />
              )}
              <ul>
                {shown.map((e) => (
                  <li key={e.key}>
                    <button
                      data-entry={e.key}
                      className={e.key === current ? "current" : undefined}
                      aria-current={e.key === current ? "location" : undefined}
                      onClick={() => go(e.key)}
                    >
                      <span className="g-nav-label">{e.label}</span>
                      {e.mark === "needed" && (
                        <span className="g-nav-dot needed" title="Something here is needed or waiting for you" />
                      )}
                      {e.mark === "own" && <span className="g-nav-dot own" title="Has its own wording" />}
                    </button>
                  </li>
                ))}
                {q && shown.length === 0 && <li className="g-nav-none">No match</li>}
              </ul>
            </div>
          );
        })}
      </nav>
      <label className="g-nav-select">
        <span>Jump to</span>
        <select value={current ?? ""} onChange={(e) => go(e.target.value)}>
          {groups.map((g) => (
            <optgroup key={g.title} label={g.title}>
              {g.entries.map((e) => (
                <option key={e.key} value={e.key}>
                  {e.label}
                  {e.mark === "needed" ? " (needed)" : e.mark === "own" ? " (own wording)" : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
    </>
  );
}
