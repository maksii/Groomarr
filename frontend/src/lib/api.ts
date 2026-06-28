import type {
  ConfigResponse,
  FindTorrentResponse,
  ManualRenameResponse,
  PreviewRenameResponse,
  RenameMode,
  RulesConfig,
  SaveConfigResponse,
  SettingsView,
  SimulateRelease,
  SimulateResponse,
  StatusView,
  TorrentSampleResponse,
  ValidatePatternResponse,
} from "./types";

interface ValidationDetail {
  loc?: (string | number)[];
  msg?: string;
}

/** Error carrying the HTTP status and parsed body for richer UI handling. */
export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  /** Human-readable field-level messages from a FastAPI 422 response. */
  validationMessages(): string[] {
    const body = this.body as { detail?: ValidationDetail[] | string } | undefined;
    if (!body || !Array.isArray(body.detail)) return [];
    return body.detail.map((d) => {
      const loc = (d.loc ?? []).filter((p) => p !== "body").join(" › ");
      const msg = (d.msg ?? "invalid value").replace(/^Value error,\s*/i, "");
      return loc ? `${loc}: ${msg}` : msg;
    });
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (e) {
    throw new ApiError("Network error — is the server reachable?", 0, String(e));
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail =
      (data as { reason?: string; detail?: unknown })?.reason ??
      (typeof (data as { detail?: unknown })?.detail === "string"
        ? (data as { detail: string }).detail
        : undefined) ??
      `Request failed (${res.status})`;
    throw new ApiError(String(detail), res.status, data);
  }

  return data as T;
}

export const api = {
  getConfig: () => request<ConfigResponse>("/api/config"),

  saveConfig: (config: RulesConfig) =>
    request<SaveConfigResponse>("/api/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  simulate: (release: SimulateRelease, config?: RulesConfig) =>
    request<SimulateResponse>("/api/rules/simulate", {
      method: "POST",
      body: JSON.stringify({ release, config: config ?? null }),
    }),

  validatePattern: (pattern: string, kind: "regex" | "match") =>
    request<ValidatePatternResponse>("/api/rules/validate-pattern", {
      method: "POST",
      body: JSON.stringify({ pattern, kind }),
    }),

  torrentSample: (query: string) =>
    request<TorrentSampleResponse>("/api/torrent-sample", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  getSettings: () => request<SettingsView>("/api/settings"),

  getStatus: () => request<StatusView>("/api/status"),

  reload: () => request<{ status: string; message: string }>("/reload"),

  // Tools — existing endpoints
  previewRename: (torrent_hash: string, new_name: string, mode: RenameMode) =>
    request<PreviewRenameResponse>("/rename/preview", {
      method: "POST",
      body: JSON.stringify({ torrent_hash, new_name, mode }),
    }),

  manualRename: (torrent_hash: string, new_name: string, mode: RenameMode) =>
    request<ManualRenameResponse>("/rename/manual", {
      method: "POST",
      body: JSON.stringify({ torrent_hash, new_name, mode }),
    }),

  findTorrent: (torrent_id: string) =>
    request<FindTorrentResponse>("/find/torrent", {
      method: "POST",
      body: JSON.stringify({ torrent_id }),
    }),
};
