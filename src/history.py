"""Operation history / audit store for Groomarr.

The webhook → decision → rename pipeline is otherwise *stateless*: every action is
emitted to the Python logger and then forgotten. This module adds a durable,
queryable record of what Groomarr did and why, backing the dashboard:

* every received webhook (Sonarr / Radarr / Prowlarr / test) and manual action,
* the trigger decision (processed vs. skipped) with the per-filter breakdown,
* the rename rule trace (which rule changed the name, step by step),
* the resulting torrent / folder / file renames (the executed plan), and
* enough of the executed plan to safely *roll back* a rename later.

Design constraints (these are load-bearing):

* **Never break the rename.** Recording is strictly best-effort — every write is
  wrapped so a storage failure logs a warning and is swallowed. The rename is the
  product; the audit log is observability.
* **No new dependency.** Uses the standard-library :mod:`sqlite3`. The database
  lives next to the rules file (on the mounted ``/config`` volume by default) so
  it survives container restarts; the path is overridable via ``HISTORY_DB``.
* **Cheap and self-healing.** The schema is created lazily on first use and is
  idempotent, so endpoints work even when the app's lifespan hook did not run
  (e.g. under the test transport).
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)

# A single shared connection guarded by a lock. Write volume is tiny (one row per
# webhook), so serializing access is simpler and safer than a pool, and WAL keeps
# reads from blocking the occasional write.
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None

# Bounds so a query can never be coerced into returning an unbounded result set.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Columns that hold JSON-encoded structures. Callers pass native Python objects;
# they are serialized on write and left as raw strings on read (the API layer
# parses them) to keep this module dependency-free of the response models.
_JSON_COLUMNS = frozenset(
    {"rule_steps_json", "trigger_checks_json", "file_plan_json", "applied_json"}
)

# Whitelist of writable columns. Anything outside this set is ignored on write so
# a caller typo can never inject SQL or a bogus column.
_COLUMNS = (
    "source",
    "event_type",
    "status",
    "decision",
    "skip_reason",
    "media_title",
    "release_title",
    "indexer",
    "tracker_name",
    "used_global",
    "download_client",
    "quality",
    "release_group",
    "torrent_hash",
    "rename_mode",
    "dry_run",
    "old_name",
    "new_name",
    "folder_old",
    "folder_new",
    "layout_kind",
    "files_total",
    "files_renamed",
    "files_failed",
    "files_skipped",
    "error",
    "rule_steps_json",
    "trigger_checks_json",
    "file_plan_json",
    "applied_json",
    "rollback_of",
    "rolled_back",
    "rolled_back_at",
    "rollback_op",
)
_WRITABLE = frozenset(_COLUMNS)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    skip_reason TEXT NOT NULL DEFAULT '',
    media_title TEXT NOT NULL DEFAULT '',
    release_title TEXT NOT NULL DEFAULT '',
    indexer TEXT NOT NULL DEFAULT '',
    tracker_name TEXT,
    used_global INTEGER NOT NULL DEFAULT 1,
    download_client TEXT NOT NULL DEFAULT '',
    quality TEXT NOT NULL DEFAULT '',
    release_group TEXT NOT NULL DEFAULT '',
    torrent_hash TEXT NOT NULL DEFAULT '',
    rename_mode TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 0,
    old_name TEXT NOT NULL DEFAULT '',
    new_name TEXT NOT NULL DEFAULT '',
    folder_old TEXT,
    folder_new TEXT,
    layout_kind TEXT NOT NULL DEFAULT '',
    files_total INTEGER NOT NULL DEFAULT 0,
    files_renamed INTEGER NOT NULL DEFAULT 0,
    files_failed INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    rule_steps_json TEXT NOT NULL DEFAULT '[]',
    trigger_checks_json TEXT NOT NULL DEFAULT '[]',
    file_plan_json TEXT NOT NULL DEFAULT '[]',
    applied_json TEXT NOT NULL DEFAULT '{}',
    rollback_of INTEGER,
    rolled_back INTEGER NOT NULL DEFAULT 0,
    rolled_back_at TEXT,
    rollback_op INTEGER
);
CREATE INDEX IF NOT EXISTS idx_operations_created_at ON operations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_operations_status ON operations (status);
CREATE INDEX IF NOT EXISTS idx_operations_source ON operations (source);
CREATE INDEX IF NOT EXISTS idx_operations_hash ON operations (torrent_hash);
"""


def _now_iso() -> str:
    """Current UTC time as a sortable ISO-8601 string (stored verbatim)."""
    return datetime.now(UTC).isoformat()


def _db_path() -> str:
    """Resolve the database path from settings at call time.

    ``HISTORY_DB`` wins if set; otherwise the database sits beside the rules file
    so a single mounted volume persists both. Read dynamically (not cached at
    import) so tests that reload settings — pointing at a temp dir — are honored.
    """
    explicit = (getattr(config.settings, "history_db", "") or "").strip()
    if explicit:
        return explicit
    return str(Path(config.settings.rules_file).parent / "groomarr_history.db")


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL: readers (dashboard) never block the occasional writer (webhook).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _get_conn() -> sqlite3.Connection:
    """Return the shared connection, (re)opening it if the target path changed.

    Caller must hold ``_lock``.
    """
    global _conn, _conn_path
    path = _db_path()
    if _conn is not None and _conn_path == path:
        return _conn
    if _conn is not None:
        with contextlib.suppress(Exception):  # closing must never raise
            _conn.close()
        _conn = None
    _conn = _connect(path)
    _conn_path = path
    return _conn


def init_db() -> None:
    """Eagerly create the database/schema (called from the app lifespan).

    Best-effort: a failure here must not prevent the service from starting.
    """
    try:
        with _lock:
            _get_conn()
        logger.info(f"Operation history database ready at {_db_path()}")
    except Exception as e:  # noqa: BLE001 - storage init must never crash startup
        logger.warning(f"Could not initialize operation history database: {e}")


def reset_for_tests() -> None:
    """Close the cached connection (so the next call reopens at the current path)."""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            with contextlib.suppress(Exception):
                _conn.close()
        _conn = None
        _conn_path = None


def _encode(field: str, value: Any) -> Any:
    """Serialize JSON columns; pass scalars through; coerce bools to 0/1."""
    if field in _JSON_COLUMNS and not isinstance(value, str):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return "[]" if field != "applied_json" else "{}"
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def record_operation(**fields: Any) -> int | None:
    """Insert a new operation row and return its id (or ``None`` on failure).

    Unknown fields are ignored. Never raises — recording is best-effort.
    """
    now = _now_iso()
    cols = ["created_at", "updated_at"]
    vals: list[Any] = [now, now]
    for key, value in fields.items():
        if key in _WRITABLE:
            cols.append(key)
            vals.append(_encode(key, value))
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    try:
        with _lock:
            conn = _get_conn()
            cur = conn.execute(f"INSERT INTO operations ({col_sql}) VALUES ({placeholders})", vals)
            conn.commit()
            new_id = int(cur.lastrowid) if cur.lastrowid is not None else None
        _prune_async_safe()
        return new_id
    except Exception as e:  # noqa: BLE001 - recording must never break the pipeline
        logger.warning(f"Failed to record operation: {e}")
        return None


def update_operation(op_id: int | None, **fields: Any) -> None:
    """Update an existing operation row. No-op if ``op_id`` is falsy. Never raises."""
    if not op_id:
        return
    sets = ["updated_at = ?"]
    vals: list[Any] = [_now_iso()]
    for key, value in fields.items():
        if key in _WRITABLE:
            sets.append(f"{key} = ?")
            vals.append(_encode(key, value))
    if len(sets) == 1:
        return
    vals.append(op_id)
    try:
        with _lock:
            conn = _get_conn()
            conn.execute(f"UPDATE operations SET {', '.join(sets)} WHERE id = ?", vals)
            conn.commit()
    except Exception as e:  # noqa: BLE001 - updates are best-effort
        logger.warning(f"Failed to update operation {op_id}: {e}")


def get_operation(op_id: int) -> dict[str, Any] | None:
    """Fetch a single operation as a dict, or ``None`` if missing / on error."""
    try:
        with _lock:
            conn = _get_conn()
            row = conn.execute("SELECT * FROM operations WHERE id = ?", (op_id,)).fetchone()
        return dict(row) if row is not None else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to read operation {op_id}: {e}")
        return None


def list_operations(
    *,
    q: str | None = None,
    source: str | None = None,
    status: str | None = None,
    decision: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Search / filter / paginate operations, newest first.

    ``q`` does a case-insensitive substring match across the human-meaningful
    text columns. Returns ``{"items", "total", "limit", "offset"}``; on error
    returns an empty page rather than raising (the dashboard degrades gracefully).
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    offset = max(0, int(offset or 0))

    where: list[str] = []
    params: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        where.append(
            "(media_title LIKE ? OR release_title LIKE ? OR new_name LIKE ? "
            "OR old_name LIKE ? OR torrent_hash LIKE ? OR indexer LIKE ? "
            "OR tracker_name LIKE ?)"
        )
        params.extend([like] * 7)
    if source:
        where.append("source = ?")
        params.append(source)
    if status:
        where.append("status = ?")
        params.append(status)
    if decision:
        where.append("decision = ?")
        params.append(decision)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if until:
        where.append("created_at <= ?")
        params.append(until)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        with _lock:
            conn = _get_conn()
            total = int(
                conn.execute(f"SELECT COUNT(*) FROM operations{where_sql}", params).fetchone()[0]
            )
            rows = conn.execute(
                f"SELECT * FROM operations{where_sql} "
                f"ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to list operations: {e}")
        return {"items": [], "total": 0, "limit": limit, "offset": offset}


def stats() -> dict[str, Any]:
    """Aggregate counts for the dashboard KPI cards. Never raises."""
    empty = {
        "total": 0,
        "last_24h": 0,
        "renamed": 0,
        "skipped": 0,
        "failed": 0,
        "rolled_back": 0,
        "by_status": {},
        "by_source": {},
        "last_operation_at": None,
    }
    try:
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        with _lock:
            conn = _get_conn()
            total = int(conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
            last_24h = int(
                conn.execute(
                    "SELECT COUNT(*) FROM operations WHERE created_at >= ?", (cutoff,)
                ).fetchone()[0]
            )
            by_status = {
                r["status"] or "unknown": r["n"]
                for r in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM operations GROUP BY status"
                ).fetchall()
            }
            by_source = {
                r["source"] or "unknown": r["n"]
                for r in conn.execute(
                    "SELECT source, COUNT(*) AS n FROM operations GROUP BY source"
                ).fetchall()
            }
            last_row = conn.execute(
                "SELECT created_at FROM operations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            "total": total,
            "last_24h": last_24h,
            "renamed": by_status.get("renamed", 0),
            "skipped": by_status.get("skipped", 0),
            "failed": by_status.get("failed", 0),
            "rolled_back": by_status.get("rolled_back", 0),
            "by_status": by_status,
            "by_source": by_source,
            "last_operation_at": last_row["created_at"] if last_row else None,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to compute operation stats: {e}")
        return empty


def _prune_async_safe() -> None:
    """Enforce retention bounds (best-effort, called after each insert).

    Two independent caps, both disabled by default for a fresh install:
    * ``HISTORY_RETENTION_DAYS`` — delete rows older than N days.
    * ``HISTORY_RETENTION_MAX_ROWS`` — keep only the newest N rows.
    """
    try:
        days = int(getattr(config.settings, "history_retention_days", 0) or 0)
        max_rows = int(getattr(config.settings, "history_retention_max_rows", 0) or 0)
        if days <= 0 and max_rows <= 0:
            return
        with _lock:
            conn = _get_conn()
            if days > 0:
                cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
                conn.execute("DELETE FROM operations WHERE created_at < ?", (cutoff,))
            if max_rows > 0:
                # Keep the newest ``max_rows`` by id (monotonic with insertion).
                conn.execute(
                    "DELETE FROM operations WHERE id NOT IN "
                    "(SELECT id FROM operations ORDER BY id DESC LIMIT ?)",
                    (max_rows,),
                )
            conn.commit()
    except Exception as e:  # noqa: BLE001 - pruning is best-effort
        logger.debug(f"History prune skipped: {e}")
