import { describe, expect, it } from "vitest";

import { currentOf } from "./SectionNav";

describe("currentOf", () => {
  const keys = ["status", "item:1", "version:a", "version:b"];
  const at = (tops: Record<string, number>) => (k: string) => tops[k] ?? null;

  it("is the last section whose top has crossed the line near the top of the window", () => {
    expect(currentOf(keys, at({ status: -400, "item:1": -50, "version:a": 300, "version:b": 900 }), false)).toBe("item:1");
    expect(currentOf(keys, at({ status: -900, "item:1": -600, "version:a": 12, "version:b": 700 }), false)).toBe(
      "version:a",
    );
    // A short section a jump put at the top keeps it, though the next one is close below.
    expect(currentOf(keys, at({ status: -900, "item:1": 12, "version:a": 90, "version:b": 700 }), false)).toBe("item:1");
    // Under the narrow screen's "Jump to" bar the line is lower.
    expect(currentOf(keys, at({ status: -900, "item:1": -600, "version:a": 60, "version:b": 700 }), false, 70)).toBe(
      "version:a",
    );
  });

  it("is the first section at the top of the page, and the last one at the very bottom", () => {
    expect(currentOf(keys, at({ status: 20, "item:1": 400, "version:a": 900, "version:b": 1400 }), false)).toBe("status");
    expect(currentOf(keys, at({ status: -2000, "item:1": -1500, "version:a": -200, "version:b": 400 }), true)).toBe(
      "version:b",
    );
  });

  it("skips sections that are not on the page", () => {
    expect(currentOf(keys, at({ status: -400, "version:b": -10 }), false)).toBe("version:b");
    expect(currentOf([], at({}), false)).toBeNull();
  });
});
