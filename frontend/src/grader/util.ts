/** The sentence to show for a failure: the message, then the reasons a refusal names. */
export const errMsg = (e: unknown) => {
  if (!(e instanceof Error)) return String(e);
  const reasons = (e as { reasons?: { message: string }[] }).reasons ?? [];
  return [e.message, ...reasons.map((r) => r.message)].join(" ");
};
export const fmtInt = (n: number | undefined) => (n ?? 0).toLocaleString();
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
export const f2 = (x: number | null | undefined) => (x === null || x === undefined ? "–" : x.toFixed(2));

/** The id to show for a response: the owner's own id when this tab holds the key, else the one-time id. */
export const displayId = (idMap: Record<string, string>, r: { id: string; external_id: string }) =>
  idMap[r.external_id] ?? (r.external_id || r.id);
