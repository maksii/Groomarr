import { useEffect, useState } from "react";
import { type PatternValidity, validatePatternCached } from "@/lib/patternCache";
import { useDebouncedValue } from "./useDebouncedValue";

/** Validate a list of patterns against the backend engine (debounced + cached). */
export function useValidatedPatterns(
  patterns: string[],
  kind?: "regex" | "match",
): Record<string, PatternValidity> {
  const [map, setMap] = useState<Record<string, PatternValidity>>({});
  const debounced = useDebouncedValue(patterns, 350);

  useEffect(() => {
    if (!kind || debounced.length === 0) {
      setMap({});
      return;
    }
    let cancelled = false;
    void (async () => {
      const entries = await Promise.all(
        debounced.map(async (p) => [p, await validatePatternCached(p, kind)] as const),
      );
      if (!cancelled) setMap(Object.fromEntries(entries));
    })();
    return () => {
      cancelled = true;
    };
  }, [debounced, kind]);

  return map;
}
