import { api } from "./api";

export interface PatternValidity {
  valid: boolean;
  error?: string | null;
  interpreted?: string | null;
}

// Module-level cache so identical patterns are validated once across the app.
const cache = new Map<string, PatternValidity>();

export async function validatePatternCached(
  pattern: string,
  kind: "regex" | "match",
): Promise<PatternValidity> {
  const key = `${kind}:${pattern}`;
  const hit = cache.get(key);
  if (hit) return hit;
  try {
    const res = await api.validatePattern(pattern, kind);
    const v: PatternValidity = {
      valid: res.valid,
      error: res.error,
      interpreted: res.interpreted,
    };
    cache.set(key, v);
    return v;
  } catch {
    // On network error, don't block the user — assume valid.
    return { valid: true };
  }
}
