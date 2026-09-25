export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
export const fmtInt = (n: number | undefined) => (n ?? 0).toLocaleString();
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
export const f2 = (x: number | null | undefined) => (x === null || x === undefined ? "–" : x.toFixed(2));
