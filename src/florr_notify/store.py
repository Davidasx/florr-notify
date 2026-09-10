"""SQLite store for florr-notify incidents.

One row per Discord message (i.e. one Super/Eternal/Unique mob lifecycle),
identified by message_id. A row may be created by a spawn observation, a
kill observation, or both.

Schema (v4):
  mob             : full display name including variant (e.g. "Mecha Wasp",
                    "Hel Spider", "Soldier Termite"). The kill embed names
                    the precise mob; for spawn-only rows this is the base
                    mob (the variant is not visible at spawn time).
  rarity          : "super" | "unique" | "eternal"
  region          : server, e.g. "Sierra (ASIA)"
  kill_filename   : thumbnail filename at the kill observation. The variant
                    is only revealed there; spawn-only rows carry no
                    filename (the spawn image filename is NOT stored -- it
                    is either identical to the kill's or the base mob's,
                    both derivable from the registry if ever needed).
  killers_json    : JSON list of killer display names
  spawn_at        : spawn time (message.created_at)
  killed_at       : kill time (message.edited_at -- the REAL kill broadcast
                    time, even when observed via backfill)
  raw_description : latest observed embed description (kill dedup)
  edit_count      : number of edits observed (diagnostics)

Data retention: rows that spawned more than BACKFILL_HOURS ago and never
received a kill broadcast are deleted at startup (see _cleanup_stale_alive).
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .parser import SpawnEvent, KillEvent

log = logging.getLogger(__name__)

# Data-retention / backfill window (hardcoded per design). Mobs spawned
# further back than this with no kill broadcast are deleted at startup
# (a Super mob does not survive a day). The collector backfills this whole
# window on startup, so every row in the DB is within it.
BACKFILL_HOURS: int = 24

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    message_id        INTEGER PRIMARY KEY,
    channel_id        INTEGER NOT NULL,
    guild_id          INTEGER,
    mob               TEXT,
    rarity            TEXT,
    region            TEXT,
    kill_filename     TEXT,
    killers_json      TEXT,
    color             INTEGER,
    spawn_at          TEXT,
    killed_at         TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    edit_count        INTEGER NOT NULL DEFAULT 0,
    raw_description   TEXT
);
CREATE INDEX IF NOT EXISTS idx_mob       ON incidents(mob);
CREATE INDEX IF NOT EXISTS idx_killed    ON incidents(killed_at);

-- Key/value store for cursor state (e.g. last_message_id for incremental
-- backfill across restarts).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self._cleanup_stale_alive()

    def close(self) -> None:
        self.conn.close()

    # --- schema migration ---
    def _migrate(self) -> None:
        """Idempotent in-place schema upgrades for DBs created by older
        versions. Safe to run on every startup; each step is a no-op if
        already applied."""
        try:
            cols = {
                row[1]
                for row in self.conn.execute(
                    "PRAGMA table_info(incidents)"
                ).fetchall()
            }
        except sqlite3.OperationalError:
            # Table doesn't exist yet (first run on a clean path);
            # _SCHEMA created it. Fresh tables already have rarity.
            cols = set()

        # 1. Rename legacy `tier` column to `rarity` (v2 -> v3).
        if "tier" in cols and "rarity" not in cols:
            self.conn.execute(
                "ALTER TABLE incidents RENAME COLUMN tier TO rarity"
            )
            cols.discard("tier")
            cols.add("rarity")

        # 2. If the column is somehow still missing (e.g. v1 DB), add it.
        if "rarity" not in cols:
            self.conn.execute(
                "ALTER TABLE incidents ADD COLUMN rarity TEXT"
            )

        # 3. Backfill: rows from before the rarity field existed (v2) have
        #    NULL; the old code only ever produced Super-rarity events.
        self.conn.execute(
            "UPDATE incidents SET rarity = 'super' WHERE rarity IS NULL"
        )

        # 4. Recompute mob from the kill filename. The broadcast TEXT only
        #    carries the BASE mob name (e.g. "Spider"); the actual variant
        #    ("Mecha Spider") is encoded only in the thumbnail filename.
        #    Older versions trusted the text, so existing rows may carry
        #    the base name. Idempotent.
        try:
            from .mobs import MOBS as _MOBS, parse_filename as _pf
            rows = self.conn.execute(
                "SELECT message_id, kill_filename, mob FROM incidents"
            ).fetchall()
            for r in rows:
                if not r["kill_filename"]:
                    continue
                base, _ = _pf(r["kill_filename"])
                if not base:
                    continue
                correct = _MOBS.get(base)
                if correct and correct != r["mob"]:
                    self.conn.execute(
                        "UPDATE incidents SET mob = ? WHERE message_id = ?",
                        (correct, r["message_id"]),
                    )
        except Exception:
            log.exception("mob re-derivation migration failed (non-fatal)")

        # 5. Ensure an index on rarity exists, and drop the duplicate left
        #    behind by the tier->rarity rename (same column, two names).
        self.conn.execute("DROP INDEX IF EXISTS idx_tier")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rarity ON incidents(rarity)"
        )

        # 6. Drop the spawn_filename column (v3 -> v4): it is derivable
        #    from the registry, and spawn-only rows expire after 24h anyway.
        try:
            self.conn.execute(
                "ALTER TABLE incidents DROP COLUMN spawn_filename"
            )
        except sqlite3.OperationalError:
            pass  # already dropped, or a fresh DB that never had it

    # --- data retention ---
    def _cleanup_stale_alive(self) -> None:
        """Delete incidents that spawned more than BACKFILL_HOURS ago and
        never received a kill broadcast.

        A Super mob does not survive a day, so its kill broadcast was
        missed (the process was down). Such a row carries no variant
        information (the variant is only revealed at kill time); deleting
        it -- instead of fabricating a death time -- keeps the predictor's
        variant counts unbiased, and guarantees every remaining row is
        within the BACKFILL_HOURS window.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=BACKFILL_HOURS)
        ).isoformat()
        self.conn.execute(
            "DELETE FROM incidents WHERE killed_at IS NULL AND spawn_at < ?",
            (cutoff,),
        )

    # --- meta (cursor state) ---
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_alive_message_ids(self) -> list[int]:
        """Message ids of mobs still marked alive (killed_at IS NULL).
        Used by the collector's alive-check at startup: each id is fetched
        individually to see whether the message has since been edited into
        a kill broadcast."""
        rows = self.conn.execute(
            "SELECT message_id FROM incidents WHERE killed_at IS NULL"
        ).fetchall()
        return [r["message_id"] for r in rows]

    # --- spawn ---
    def upsert_spawn(self, e: SpawnEvent) -> bool:
        """Record a spawn observation. Returns True iff a new incident row
        was inserted."""
        cur = self.conn.execute(
            "SELECT spawn_at FROM incidents WHERE message_id = ?",
            (e.message_id,),
        ).fetchone()
        if cur is None:
            self.conn.execute(
                """INSERT INTO incidents
                   (message_id, channel_id, guild_id, mob, rarity, region,
                    color, spawn_at, first_seen_at,
                    last_seen_at, edit_count, raw_description)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0,?)""",
                (e.message_id, e.channel_id, e.guild_id, e.mob, e.rarity,
                 e.region, e.color, e.observed_at,
                 e.observed_at, e.observed_at, e.raw_description),
            )
            return True
        # Row exists: fill in spawn-only fields where currently NULL. We do
        # NOT overwrite a mob set by a prior kill observation (the kill is
        # authoritative for the variant).
        self.conn.execute(
            """UPDATE incidents SET
                 spawn_at        = COALESCE(spawn_at, ?),
                 mob             = COALESCE(mob, ?),
                 rarity          = COALESCE(rarity, ?),
                 region          = COALESCE(region, ?),
                 color           = COALESCE(color, ?),
                 raw_description = COALESCE(raw_description, ?),
                 last_seen_at    = ?
               WHERE message_id = ?""",
            (e.observed_at, e.mob, e.rarity, e.region,
             e.color, e.raw_description, e.observed_at, e.message_id),
        )
        return False

    # --- kill ---
    def upsert_kill(self, e: KillEvent) -> bool:
        """Record a kill observation. Returns True iff this is a new kill
        transition; False for duplicate kill edits (same raw_description)."""
        cur = self.conn.execute(
            "SELECT killed_at, raw_description FROM incidents WHERE message_id = ?",
            (e.message_id,),
        ).fetchone()
        killers_json = json.dumps(e.killers, ensure_ascii=False)

        if cur is None:
            # Kill observed without a prior spawn (e.g. the spawn was
            # outside the backfill window): record what we know. The kill
            # time is still accurate (message.edited_at).
            self.conn.execute(
                """INSERT INTO incidents
                   (message_id, channel_id, guild_id, mob, rarity, region,
                    kill_filename, killers_json, color, killed_at,
                    first_seen_at, last_seen_at, edit_count, raw_description)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (e.message_id, e.channel_id, e.guild_id, e.mob, e.rarity,
                 e.region, e.image_filename, killers_json, e.color,
                 e.observed_at, e.observed_at, e.observed_at,
                 e.raw_description),
            )
            return True

        # Dedupe: identical kill content already recorded.
        if cur["killed_at"] is not None and cur["raw_description"] == e.raw_description:
            return False

        prev_killed = cur["killed_at"] is not None
        self.conn.execute(
            """UPDATE incidents SET
                 kill_filename   = ?,
                 killers_json    = ?,
                 killed_at       = ?,
                 color           = COALESCE(color, ?),
                 -- kill is the AUTHORITATIVE source for the variant (the
                 -- variant only appears in the kill image filename, not in
                 -- the broadcast text): always overwrite mob.
                 mob             = ?,
                 rarity          = COALESCE(rarity, ?),
                 region          = COALESCE(region, ?),
                 raw_description = ?,
                 last_seen_at    = ?,
                 edit_count      = edit_count + 1
               WHERE message_id = ?""",
            (e.image_filename, killers_json, e.observed_at,
             e.color, e.mob, e.rarity, e.region, e.raw_description,
             e.observed_at, e.message_id),
        )
        return not prev_killed

    # --- queries ---
    def count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM incidents"
        ).fetchone()["c"]

    def recent(self, n: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM incidents "
            "ORDER BY COALESCE(killed_at, spawn_at, first_seen_at) DESC LIMIT ?",
            (n,),
        ).fetchall()

    def alive(self) -> list[sqlite3.Row]:
        """Mobs currently believed alive (spawned within the retention
        window, no kill broadcast), newest spawn first."""
        return self.conn.execute(
            "SELECT mob, rarity, region, spawn_at FROM incidents "
            "WHERE killed_at IS NULL AND spawn_at IS NOT NULL "
            "ORDER BY spawn_at DESC"
        ).fetchall()

    def recent_kills(self) -> list[sqlite3.Row]:
        """Latest kill per (mob, region). The 30-min cooldown window is
        applied by the caller against `killed_at` (see predictor.COOLDOWN)."""
        return self.conn.execute(
            "SELECT mob, rarity, region, MAX(killed_at) AS killed_at "
            "FROM incidents WHERE killed_at IS NOT NULL "
            "GROUP BY mob, region ORDER BY killed_at DESC"
        ).fetchall()
