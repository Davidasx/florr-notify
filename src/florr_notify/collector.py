"""discord.py-self client: streams the #game channel into the store + notifier.

Read-only by design:
  - No message sending.
  - No guild join/leave.
  - Only gateway events (MESSAGE_CREATE, MESSAGE_UPDATE) + startup backfill.
  - REST is used implicitly only for the startup backfill via channel.history().

Restart safety (two cheap startup passes, no full-window re-read):
  1. Gap fetch -- history(after=last_message_id) pulls only messages
     CREATED while we were down (new spawns/kills of unseen mobs).
  2. Alive check -- every mob still marked alive is fetched individually
     by message id; if its message was edited into a kill while we were
     down, the kill is recorded (with the real edited_at time). This
     catches edits that a creation-based cursor would miss.

Data retention: mobs spawned more than BACKFILL_HOURS ago with no kill
broadcast are deleted at startup (see store._cleanup_stale_alive).

Whitelists ([whitelist] spawn/kill in config) filter NOTIFICATIONS only --
everything is always stored and logged. Silent mode ([notify] silent)
suppresses desktop notifications entirely.

Live spawns of multi-variant mobs include a variant prediction
    (probabilities + confidence) in the notification body and the log.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import Message

from .config import Config
from .mobs import BASE_OF, base_mob as _base_mob
from .parser import KillEvent, SpawnEvent, parse_embed
from .predictor import predict, format_prediction_line
from .store import Store, BACKFILL_HOURS
from .notify import Notifier

log = logging.getLogger(__name__)


def _embed_view(emb) -> dict:
    """Flatten a discord.Embed to a dict for parse_embed()."""
    d: dict = {
        "type": "rich",
        "description": emb.description or "",
        "color": int(emb.color or 0),
    }
    if emb.thumbnail:
        d["thumbnail"] = {
            "url": emb.thumbnail.url or "",
            "proxy_url": emb.thumbnail.proxy_url or "",
        }
    if emb.footer:
        d["footer"] = {"text": emb.footer.text or ""}
    return d


# Discord snowflake epoch: 2015-01-01T00:00:00Z (milliseconds).
_DISCORD_EPOCH_MS = 1420070400000



def _snowflake_from_dt(dt: datetime) -> int:
    """Convert a moment to a Discord snowflake, usable as a history
    'after' cursor (snowflakes are time-ordered, like message ids)."""
    return (int(dt.timestamp() * 1000) - _DISCORD_EPOCH_MS) << 22

# Hard cap on backfill messages per startup (gap fetch and first-run seed).
# history(after=...) pages newest-first, so the cap keeps the NEWEST
# messages and drops the oldest ones from a long downtime -- the right
# trade-off: kills older than the 30-min cooldown are irrelevant, and
# mobs older than BACKFILL_HOURS are cleaned up anyway.
BACKFILL_MAX_MESSAGES = 100

class Collector(discord.Client):
    def __init__(self, cfg: Config, store: Store, notifier: Notifier):
        # dpy-self 2.x: self-bots automatically receive all relevant gateway
        # events (MESSAGE_CREATE/UPDATE, GUILD_MESSAGES, MESSAGE_CONTENT, etc.).
        super().__init__()
        self.cfg = cfg
        self.store = store
        self.notifier = notifier

        # Flag spawn-whitelist entries that can never match: cosmetic
        # variants are not visible at spawn time (the spawn image is the
        # base mob's). users should list the base mob instead.
        for entry in self.cfg.whitelist.ignored_spawn_entries():
            log.warning(
                "spawn whitelist entry %r is a cosmetic variant and will be "
                "ignored for spawns (the variant is only revealed at kill "
                "time); add the base mob %r instead",
                entry, BASE_OF[entry],
            )
    # ---- lifecycle ----
    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        await self._backfill()
        log.info("Backfill complete; entering live mode.")

    async def _backfill(self) -> None:
        ch = self.get_channel(self.cfg.discord.game_channel_id)
        if ch is None:
            log.error(
                "Game channel %s not in cache (is the account a member of the "
                "florr.io server? did it finish guild chunking?)",
                self.cfg.discord.game_channel_id,
            )
            return
        # 1. Downtime gap: fetch only messages CREATED since our cursor.
        #    This catches new spawns and new kills of previously-unseen
        #    mobs. (First run: no cursor -> seed the whole BACKFILL_HOURS
        #    window instead.)
        last_id = self.store.get_meta("last_message_id")
        if last_id is None:
            cutoff = datetime.now(timezone.utc) - timedelta(
                hours=BACKFILL_HOURS
            )
            after = _snowflake_from_dt(cutoff)
            log.info("First run: seeding history for the last %dh.",
                     BACKFILL_HOURS)
        else:
            after = int(last_id)
        try:
            # Hard cap: keep the NEWEST messages of the gap; older ones
            # are cooldown-irrelevant and outside the retention window's
            # value anyway.
            async for msg in ch.history(limit=BACKFILL_MAX_MESSAGES,
                                        after=discord.Object(id=after)):
                await self._handle(msg, notify=False)  # silent backfill
        except Exception:
            log.exception("gap backfill failed")

        # 2. Alive check: every mob still marked alive (killed_at IS NULL)
        #    has its message_id stored. A kill that happened while we were
        #    down is an EDIT of that message -- visible here even though the
        #    message id predates our cursor (history 'after=' filters by
        #    creation, so a plain gap fetch would miss it). One cheap REST
        #    call per alive mob instead of re-reading the whole window.
        alive_ids = self.store.get_alive_message_ids()
        log.info("Alive-check: fetching %d mob message(s) by id.",
                 len(alive_ids))
        for mid in alive_ids:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                log.warning(
                    "alive mob message %s not found (deleted?); row kept", mid,
                )
                continue
            except Exception:
                log.exception(
                    "alive-check fetch failed for %s (non-fatal)", mid,
                )
                continue
            await self._handle(msg, notify=False)  # re-parse current state

        log.info("Backfill complete (no notifications).")
    # ---- gateway events ----
    async def on_message(self, message: Message) -> None:
        if message.channel.id != self.cfg.discord.game_channel_id:
            return
        await self._handle(message)

    async def on_raw_message_edit(self, event: discord.RawMessageUpdateEvent) -> None:
        # discord.py-self dispatches on_message_edit ONLY for messages in its
        # in-memory cache (state.parse_message_update falls back to just the
        # raw event when the cache lookup misses). The cache is empty after
        # every restart, so kill edits of mobs spawned before the restart
        # would be silently missed. The raw event fires for EVERY edit:
        # rebuild a Message view from the gateway payload and run the same
        # pipeline. Upserts are idempotent, so double-handling is harmless.
        data = event.data
        # gateway snowflakes are STRINGS; cfg id is an int -- compare as ints
        if int(data.get("channel_id", 0)) != self.cfg.discord.game_channel_id:
            return
        channel = self.get_channel(int(data["channel_id"]))
        if channel is None:   # guild/channel not in cache yet
            return
        msg = discord.Message(channel=channel, data=data, state=self._connection)
        await self._handle(msg)

    # ---- core ----
    async def _handle(self, message: Message, *, notify: bool = True) -> None:
        self._track_message_id(message)
        ts = (message.edited_at or message.created_at).isoformat()
        for emb in message.embeds:
            try:
                e = parse_embed(
                    _embed_view(emb),
                    message_id=message.id,
                    channel_id=message.channel.id,
                    guild_id=(message.guild.id if message.guild else None),
                    observed_at=ts,
                )
            except Exception:
                log.exception("parse failed for msg %s", message.id)
                continue
            if e is None:
                continue
            if isinstance(e, SpawnEvent):
                self.store.upsert_spawn(e)
                if notify:
                    await self._notify_spawn(e)
                else:
                    log.info(
                        "SPAWN  msg=%s  rarity=%-7s mob=%-18s region=%s (backfill)",
                        e.message_id, e.rarity, repr(e.mob), e.region,
                    )
            elif isinstance(e, KillEvent):
                is_new = self.store.upsert_kill(e)
                if is_new:
                    if notify:
                        await self._notify_kill(e)
                    else:
                        log.info(
                            "KILL   msg=%s  rarity=%-7s mob=%-18s region=%s "
                            "killers=%s (backfill)",
                            e.message_id, e.rarity, repr(e.mob), e.region,
                            e.killers,
                        )
                else:
                    log.debug(
                        "KILL   msg=%s  duplicate (raw_description unchanged)",
                        e.message_id,
                    )

    # ---- spawn notification with variant prediction ----
    async def _notify_spawn(self, e: SpawnEvent) -> None:
        # The whitelist throttles Super-rarity NOTIFICATIONS only: filtered
        # mobs are still logged in full (region + prediction) -- the only
        # difference is no desktop notification. Unique/Eternal always
        # announce regardless of the whitelist.
        throttled = (
            e.rarity == "super"
            and not self.cfg.whitelist.allows_spawn(e.mob)
        )
        prediction = self._predict_line(e)
        suffix = "  [filtered by spawn whitelist]" if throttled else ""
        log.info(
            "SPAWN  msg=%s  rarity=%-7s mob=%-18s region=%s%s%s",
            e.message_id, e.rarity, repr(e.mob), e.region,
            f"\n       {prediction}" if prediction else "",
            suffix,
        )
        if not throttled:
            await self.notifier.spawn(e, prediction=prediction)

    async def _notify_kill(self, e: KillEvent) -> None:
        # Same as spawns: filtered kills are still logged in full (region,
        # killers) -- the only difference is no desktop notification.
        throttled = (
            e.rarity == "super"
            and not self.cfg.whitelist.allows_kill(e.mob)
        )
        suffix = "  [filtered by kill whitelist]" if throttled else ""
        log.info(
            "KILL   msg=%s  rarity=%-7s mob=%-18s region=%-10s killers=%s%s",
            e.message_id, e.rarity, repr(e.mob), e.region, e.killers,
            suffix,
        )
        if not throttled:
            await self.notifier.kill(e)

    def _predict_line(self, e: SpawnEvent) -> str | None:
        """Prediction line for a live spawn; None when there is nothing
        useful to show (k=1 standalone, no data, or internal error)."""
        try:
            pred = predict(
                _base_mob(e.mob),
                self.store,
                server=e.region,
                now=datetime.fromisoformat(e.observed_at),
            )
            return format_prediction_line(pred)
        except Exception:
            log.exception("prediction failed (non-fatal)")
            return None

    # ---- cursor state ----
    def _track_message_id(self, message: Message) -> None:
        """Persist the newest processed message id (snowflakes are
        time-ordered) so the next startup can resume the backfill."""
        prev = self.store.get_meta("last_message_id")
        if prev is None or message.id > int(prev):
            self.store.set_meta("last_message_id", str(message.id))
