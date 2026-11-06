"""SQLite persistence layer for Anatonix.

Design notes
------------
* One short-lived connection per operation.  Streamlit executes the script in
  worker threads, and SQLite connections cannot be shared safely across
  threads; opening a connection is cheap and keeps the code re-entrant.
* Every statement is parameterised - no string interpolation of user data.
* Writes are wrapped in explicit transactions (``with db.connection()``).
* Timestamps are stored as ISO-8601 UTC strings (``...+00:00``) which sort
  lexicographically and parse directly with :func:`pandas.to_datetime`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from backend.logging_setup import get_logger
from config import DEFAULT_SETTINGS, resolve_db_path

log = get_logger("anatonix.database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    start_timestamp   TEXT NOT NULL,
    end_timestamp     TEXT,
    duration_seconds  REAL,
    calibration_id    INTEGER,
    session_metadata  TEXT
);

CREATE TABLE IF NOT EXISTS telemetry (
    record_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    forward_lean        REAL,
    shoulder_tilt       REAL,
    ear_value           REAL,
    proximity_ratio     REAL,
    blink_rate          REAL,
    tracking_confidence REAL,
    posture_status      TEXT,
    UNIQUE (session_id, timestamp),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    alert_type   TEXT NOT NULL,
    measurement  REAL,
    threshold    REAL,
    message      TEXT,
    details      TEXT,
    client_uid   TEXT UNIQUE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS calibrations (
    calibration_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at             TEXT NOT NULL,
    baseline_pitch         REAL NOT NULL,
    baseline_shoulder_tilt REAL NOT NULL,
    baseline_shoulder_span REAL NOT NULL,
    quality_score          REAL NOT NULL,
    frames_collected       INTEGER,
    notes                  TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    message     TEXT,
    details     TEXT,
    client_uid  TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_session_ts ON telemetry(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_session_ts ON alerts(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_calibrations_created ON calibrations(created_at);
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().replace(microsecond=0).isoformat()


def to_iso(value: datetime | str | None) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class Database:
    """Thin, explicit data-access object over the Anatonix SQLite file."""

    def __init__(self, path: Optional[str | Path] = None, *, create: bool = True) -> None:
        self.path = Path(path) if path is not None else resolve_db_path()
        self._lock = threading.RLock()
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.init_schema()

    # -- connection management -------------------------------------------
    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        # The database file sits next to the application, so it can be deleted
        # or replaced while the app is running. A blank file would fail every
        # query with "no such table"; recreate the schema instead of crashing.
        try:
            if not self._schema_present(conn):
                log.warning("No schema in %s — recreating", self.path)
                conn.executescript(SCHEMA)
        except sqlite3.DatabaseError as error:
            log.error("Database unusable at %s: %s", self.path, error)
            raise
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._lock:
            with self.connection() as conn:
                conn.executescript(SCHEMA)
        log.info("Schema ready at %s", self.path)

    def _schema_present(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
        ).fetchone()
        return bool(row and row[0])

    def table_names(self) -> List[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    # -- settings ---------------------------------------------------------
    def get_settings(self) -> Dict[str, Any]:
        settings = dict(DEFAULT_SETTINGS)
        with self.connection() as conn:
            rows = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
        for row in rows:
            try:
                settings[row["setting_key"]] = json.loads(row["setting_value"])
            except (TypeError, ValueError):
                log.warning("Discarding unreadable setting %s", row["setting_key"])
        return settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT setting_value FROM settings WHERE setting_key = ?", (key,)
            ).fetchone()
        if row is None:
            return DEFAULT_SETTINGS.get(key, default)
        try:
            return json.loads(row["setting_value"])
        except (TypeError, ValueError):
            return DEFAULT_SETTINGS.get(key, default)

    def set_settings(self, values: Dict[str, Any]) -> None:
        now = utcnow_iso()
        with self._lock, self.connection() as conn:
            conn.executemany(
                """INSERT INTO settings (setting_key, setting_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                       setting_value = excluded.setting_value,
                       updated_at = excluded.updated_at""",
                [(k, json.dumps(v), now) for k, v in values.items()],
            )
        log.debug("Persisted %d setting(s)", len(values))

    def set_setting(self, key: str, value: Any) -> None:
        self.set_settings({key: value})

    def reset_settings(self) -> None:
        with self._lock, self.connection() as conn:
            conn.execute("DELETE FROM settings")
        log.info("Settings reset to defaults")

    # -- sessions ---------------------------------------------------------
    def create_session(
        self,
        session_id: str,
        start_timestamp: Optional[str] = None,
        calibration_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        start = start_timestamp or utcnow_iso()
        with self._lock, self.connection() as conn:
            conn.execute(
                """INSERT INTO sessions
                       (session_id, start_timestamp, end_timestamp, duration_seconds,
                        calibration_id, session_metadata)
                   VALUES (?, ?, NULL, NULL, ?, ?)
                   ON CONFLICT(session_id) DO NOTHING""",
                (session_id, start, calibration_id, json.dumps(metadata or {})),
            )
        return session_id

    def ensure_session(
        self,
        session_id: str,
        start_timestamp: Optional[str] = None,
        calibration_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            self.create_session(session_id, start_timestamp, calibration_id, metadata)
        return session_id

    def end_session(
        self, session_id: str, end_timestamp: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Close a session and store its duration. Idempotent."""
        end_dt = parse_iso(end_timestamp) or utcnow()
        with self._lock, self.connection() as conn:
            row = conn.execute(
                "SELECT start_timestamp, end_timestamp FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            if row["end_timestamp"]:
                return self.get_session(session_id)
            start_dt = parse_iso(row["start_timestamp"]) or end_dt
            duration = max(0.0, (end_dt - start_dt).total_seconds())
            last = conn.execute(
                "SELECT MAX(timestamp) AS ts FROM telemetry WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            last_ts = parse_iso(last["ts"]) if last and last["ts"] else None
            if last_ts and last_ts < start_dt:
                duration = 0.0
            conn.execute(
                "UPDATE sessions SET end_timestamp = ?, duration_seconds = ? WHERE session_id = ?",
                (to_iso(end_dt), round(duration, 2), session_id),
            )
        log.info("Session %s closed (%.1fs)", session_id, duration)
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._session_row(row) if row else None

    def list_sessions(
        self,
        limit: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        include_active: bool = True,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM sessions WHERE 1=1"
        params: List[Any] = []
        if since:
            query += " AND start_timestamp >= ?"
            params.append(to_iso(since))
        if until:
            query += " AND start_timestamp <= ?"
            params.append(to_iso(until))
        if not include_active:
            query += " AND end_timestamp IS NOT NULL"
        query += " ORDER BY start_timestamp DESC"
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._session_row(r) for r in rows]

    def active_session(self) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM sessions WHERE end_timestamp IS NULL
                   ORDER BY start_timestamp DESC LIMIT 1"""
            ).fetchone()
        return self._session_row(row) if row else None

    @staticmethod
    def _session_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.get("session_metadata") or "{}")
        except (TypeError, ValueError):
            data["metadata"] = {}
        return data

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self.connection() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM telemetry WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM alerts WHERE session_id = ?", (session_id,))
            conn.execute(
                "DELETE FROM events WHERE session_id = ? AND event_type != 'report_generated'",
                (session_id,),
            )
        deleted = cur.rowcount > 0
        if deleted:
            log.info("Deleted session %s", session_id)
        return deleted

    def delete_sessions(self, session_ids: Sequence[str]) -> int:
        count = 0
        for session_id in session_ids:
            count += int(self.delete_session(session_id))
        return count

    def delete_all_sessions(self) -> int:
        with self._lock, self.connection() as conn:
            cur = conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM telemetry")
            conn.execute("DELETE FROM alerts")
        log.info("Deleted %d session(s)", cur.rowcount)
        return cur.rowcount

    def finalize_stale_sessions(self, grace_seconds: int = 90) -> List[str]:
        """Close sessions whose owner stopped reporting (tab closed / crash)."""
        closed: List[str] = []
        cutoff = utcnow() - timedelta(seconds=grace_seconds)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT session_id, start_timestamp FROM sessions WHERE end_timestamp IS NULL"
            ).fetchall()
        for row in rows:
            with self.connection() as conn:
                last = conn.execute(
                    "SELECT MAX(timestamp) AS ts FROM telemetry WHERE session_id = ?",
                    (row["session_id"],),
                ).fetchone()
            last_ts = parse_iso(last["ts"]) if last and last["ts"] else None
            reference = last_ts or parse_iso(row["start_timestamp"])
            if reference is None or reference > cutoff:
                continue
            self.end_session(row["session_id"], to_iso(reference))
            self.add_event(
                row["session_id"],
                "session_recovered",
                "Session closed automatically after monitoring stopped reporting.",
                timestamp=to_iso(reference),
            )
            closed.append(row["session_id"])
        return closed

    # -- telemetry --------------------------------------------------------
    def add_telemetry(self, rows: Iterable[Dict[str, Any]]) -> int:
        """Bulk insert telemetry rows; duplicates (same session+timestamp) ignored."""
        prepared = []
        for row in rows:
            prepared.append(
                (
                    row["session_id"],
                    to_iso(row["timestamp"]),
                    row.get("forward_lean"),
                    row.get("shoulder_tilt"),
                    row.get("ear_value"),
                    row.get("proximity_ratio"),
                    row.get("blink_rate"),
                    row.get("tracking_confidence"),
                    row.get("posture_status"),
                )
            )
        if not prepared:
            return 0
        with self._lock, self.connection() as conn:
            cur = conn.executemany(
                """INSERT OR IGNORE INTO telemetry
                   (session_id, timestamp, forward_lean, shoulder_tilt, ear_value,
                    proximity_ratio, blink_rate, tracking_confidence, posture_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
            inserted = cur.rowcount
        if inserted:
            log.debug("Inserted %d telemetry record(s)", inserted)
        return inserted

    def fetch_telemetry(
        self,
        session_ids: Optional[Sequence[str]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM telemetry WHERE 1=1"
        params: List[Any] = []
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            query += f" AND session_id IN ({placeholders})"
            params.extend(session_ids)
        if since:
            query += " AND timestamp >= ?"
            params.append(to_iso(since))
        if until:
            query += " AND timestamp <= ?"
            params.append(to_iso(until))
        query += " ORDER BY timestamp ASC"
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def telemetry_count(self, session_id: Optional[str] = None) -> int:
        with self.connection() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM telemetry WHERE session_id = ?", (session_id,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM telemetry").fetchone()
        return int(row["c"])

    # -- alerts -----------------------------------------------------------
    def add_alert(
        self,
        session_id: str,
        alert_type: str,
        measurement: Optional[float] = None,
        threshold: Optional[float] = None,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
        client_uid: Optional[str] = None,
    ) -> Optional[int]:
        """Insert an alert. Returns None when ``client_uid`` was already stored."""
        with self._lock, self.connection() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO alerts
                   (session_id, timestamp, alert_type, measurement, threshold,
                    message, details, client_uid)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    to_iso(timestamp) or utcnow_iso(),
                    alert_type,
                    measurement,
                    threshold,
                    message,
                    json.dumps(details or {}),
                    client_uid,
                ),
            )
            if cur.rowcount == 0:
                return None
            return int(cur.lastrowid)

    def fetch_alerts(
        self,
        session_ids: Optional[Sequence[str]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        alert_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM alerts WHERE 1=1"
        params: List[Any] = []
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            query += f" AND session_id IN ({placeholders})"
            params.extend(session_ids)
        if since:
            query += " AND timestamp >= ?"
            params.append(to_iso(since))
        if until:
            query += " AND timestamp <= ?"
            params.append(to_iso(until))
        if alert_type:
            query += " AND alert_type = ?"
            params.append(alert_type)
        query += " ORDER BY timestamp DESC"
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details") or "{}")
            except (TypeError, ValueError):
                item["details"] = {}
            out.append(item)
        return out

    # -- events / activity -------------------------------------------------
    def add_event(
        self,
        session_id: Optional[str],
        event_type: str,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
        client_uid: Optional[str] = None,
    ) -> Optional[int]:
        """Insert an activity event. Returns None when ``client_uid`` already exists."""
        with self._lock, self.connection() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                   (session_id, timestamp, event_type, message, details, client_uid)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    to_iso(timestamp) or utcnow_iso(),
                    event_type,
                    message,
                    json.dumps(details or {}),
                    client_uid,
                ),
            )
            if cur.rowcount == 0:
                return None
            return int(cur.lastrowid)

    def fetch_events(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (int(limit),)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details") or "{}")
            except (TypeError, ValueError):
                item["details"] = {}
            out.append(item)
        return out

    # -- calibrations ------------------------------------------------------
    def add_calibration(
        self,
        baseline_pitch: float,
        baseline_shoulder_tilt: float,
        baseline_shoulder_span: float,
        quality_score: float,
        frames_collected: int,
        notes: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> int:
        with self._lock, self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO calibrations
                   (created_at, baseline_pitch, baseline_shoulder_tilt,
                    baseline_shoulder_span, quality_score, frames_collected, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    to_iso(created_at) or utcnow_iso(),
                    float(baseline_pitch),
                    float(baseline_shoulder_tilt),
                    float(baseline_shoulder_span),
                    float(quality_score),
                    int(frames_collected),
                    notes,
                ),
            )
            calibration_id = int(cur.lastrowid)
        log.info("Stored calibration %s (quality %.2f)", calibration_id, quality_score)
        return calibration_id

    def latest_calibration(self) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM calibrations ORDER BY created_at DESC, calibration_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def list_calibrations(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM calibrations ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_calibrations(self) -> int:
        with self._lock, self.connection() as conn:
            cur = conn.execute("DELETE FROM calibrations")
            conn.execute("UPDATE sessions SET calibration_id = NULL")
        log.info("Cleared %d calibration(s)", cur.rowcount)
        return cur.rowcount

    # -- maintenance -------------------------------------------------------
    def purge_older_than(self, days: int) -> int:
        cutoff = to_iso(utcnow() - timedelta(days=int(days)))
        with self._lock, self.connection() as conn:
            ids = [
                r["session_id"]
                for r in conn.execute(
                    "SELECT session_id FROM sessions WHERE start_timestamp < ?", (cutoff,)
                ).fetchall()
            ]
        return self.delete_sessions(ids)

    def storage_stats(self) -> Dict[str, Any]:
        with self.connection() as conn:
            sessions = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
            telemetry = conn.execute("SELECT COUNT(*) AS c FROM telemetry").fetchone()["c"]
            alerts = conn.execute("SELECT COUNT(*) AS c FROM alerts").fetchone()["c"]
            cals = conn.execute("SELECT COUNT(*) AS c FROM calibrations").fetchone()["c"]
            first = conn.execute("SELECT MIN(start_timestamp) AS t FROM sessions").fetchone()["t"]
        size_bytes = self.path.stat().st_size if self.path.exists() else 0
        return {
            "sessions": sessions,
            "telemetry_records": telemetry,
            "alerts": alerts,
            "calibrations": cals,
            "first_session": first,
            "db_size_bytes": size_bytes,
            "db_path": str(self.path),
        }


def default_database() -> Database:
    """Return a Database bound to the configured path (cheap; cached by caller)."""
    return Database()
