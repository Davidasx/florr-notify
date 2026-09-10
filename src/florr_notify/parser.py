"""Parse florr.io #game embeds into spawn / kill events.

See research/02-message-format.md for the authoritative spec.

CRITICAL: the broadcast TEXT uses the BASE mob name, while the thumbnail
IMAGE uses the actual variant filename. E.g. a Mecha Spider kill embeds
have description "A Super Spider has been defeated by ..." but image
"petal-spider_mecha-super-x.png". The variant is conveyed ONLY by the
image. Therefore the AUTHORITATIVE source for the mob name (including
variant) is the thumbnail filename, resolved via mobs.MOBS.

Recognized rarities: Super / Unique / Eternal.  The rarity word appears:
  - In the description as a leading word after the article
    (A Super / A Unique / An Eternal)
  - In the thumbnail filename as a suffix
    (petal-<base>-super.png / petal-<base>-unique.png / petal-<base>-eternal.png;
     kill embeds add an "-x" decoration: petal-<base>-<rarity>-x.png)

Recognized description patterns (ZWSP stripped first):
  1. Standard spawn  : "(A|An) (Super|Unique|Eternal) <BaseMob> has spawned!"
                       -> SpawnEvent, mob from filename (with variant), rarity from description
  2. Standard kill   : "(A|An) (Super|Unique|Eternal) <BaseMob> has been defeated by <players>!"
                       -> KillEvent, mob from filename (with variant),
                          rarity + killers from description
  3. Craft broadcast : "...has been crafted/forged by <player>!"  -> None  (any
     "has been <verb> by <player>" that is not a kill: petals share names and
     image filenames with mobs, e.g. petal Cactus vs mob Cactus)
  4. Flavor spawn   : prose description (Rock, Cactus, ..., Hel, plus any rarity)
                       -> SpawnEvent, mob + rarity from filename
  5. Anything else  : -> None

  NOTE on ZWSP: descriptions open with interleaved zero-width chars
  (ZWSP NL ZWSP NL for some broadcast kinds). _clean removes them
  GLOBALLY: edge-stripping alone left a leading ZWSP that broke the
  ^-anchored regexes, and spawns of mobs missing from MOBS (Ant Egg,
  Termite Mound, Termite Overmind) then fell to None -- silently,
  while their kill edits still parsed (kills use a cleanable format).
     (any "has been <verb> by <player>" that is not a kill: petals share
      names and image filenames with mobs, e.g. petal Cactus vs mob Cactus)
  4. Flavor spawn   : prose description (Rock, Cactus, ..., Hel, plus any rarity)
                       -> SpawnEvent, mob + rarity from filename
  5. Anything else  : -> None
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .mobs import MOBS, parse_filename

# Zero-width chars Discord may prepend/append to embed descriptions -- and,
# for some broadcast kinds, INTERLEAVE (spawn embeds open with ZWSP NL ZWSP
# NL, which edge-stripping cannot fully remove). Remove them everywhere:
# they never carry meaning in broadcast text.
_INVIS_CHARS = re.compile("[\u200b\u200c\u200d\u2060\ufeff]+")

_SPAWN_RE = re.compile(
    r"^(?:A|An) (Super|Unique|Eternal) (.+?) has spawned!$"
)
_KILL_RE = re.compile(
    r"^(?:A|An) (Super|Unique|Eternal) (.+?) has been defeated by (.+)!$"
)
# Craft broadcasts: "has been crafted/forged by <player>". Verbs observed in
# the wild: crafted, forged. IMPORTANT: petal broadcasts share names AND
# thumbnail filenames with mobs (the petal Cactus vs the mob Cactus), so a
# craft that slips past this check would fall through to the flavor-spawn
# fallback and be recorded as a fake mob spawn (whitelist-exempt for
# Unique/Eternal!). Defensive net: ANY "has been <verb> by <player>" that
# failed the kill pattern above is craft-like and must be ignored -- mob
# spawns never use that structure ("has spawned!" or plain flavor text).
_CRAFTISH_RE = re.compile(r"has been\s+\S+\s+by\b", re.IGNORECASE)
_PLAYER_SPLIT_RE = re.compile(r",\s+|\s+and\s+")



def _clean(s: str) -> str:
    return _INVIS_CHARS.sub("", s).strip()


def parse_players(text: str) -> list[str]:
    parts = _PLAYER_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _filename_from_url(url: str) -> str:
    if not url:
        return ""
    return os.path.basename(urlparse(url).path)


@dataclass(frozen=True)
class SpawnEvent:
    message_id: int
    channel_id: int
    guild_id: int | None
    mob: str                       # full display name, e.g. "Mecha Spider", "Hel Spider"
    rarity: str                    # "super" | "unique" | "eternal"
    region: str
    image_filename: str
    image_url: str                 # full CDN URL (with query) - for icon download; not stored
    color: int
    observed_at: str
    raw_description: str


@dataclass(frozen=True)
class KillEvent:
    message_id: int
    channel_id: int
    guild_id: int | None
    mob: str
    rarity: str
    region: str
    image_filename: str
    image_url: str
    killers: list[str]
    color: int
    observed_at: str
    raw_description: str


def parse_embed(
    embed: dict[str, Any],
    *,
    message_id: int,
    channel_id: int,
    guild_id: int | None,
    observed_at: str,
) -> SpawnEvent | KillEvent | None:
    """Parse a Discord embed dict into a SpawnEvent / KillEvent, or None."""
    desc_raw = embed.get("description") or ""
    desc = _clean(desc_raw)
    if not desc:
        return None

    thumb = embed.get("thumbnail") or {}
    url = thumb.get("url") or thumb.get("proxy_url") or ""
    fname = _filename_from_url(url)
    region = _clean((embed.get("footer") or {}).get("text", ""))
    color = int(embed.get("color") or 0)

    # AUTHORITATIVE mob name from the thumbnail filename: this is the ONLY
    # place the variant is encoded. The broadcast text uses the base mob name.
    base_from_fname, _rarity_from_fname = parse_filename(fname)
    mob_from_fname = MOBS.get(base_from_fname) if base_from_fname else None

    shared = dict(
        message_id=message_id,
        channel_id=channel_id,
        guild_id=guild_id,
        region=region,
        image_filename=fname,
        image_url=url,
        color=color,
        observed_at=observed_at,
        raw_description=desc_raw,
    )

    # 1. Standard spawn
    if m := _SPAWN_RE.match(desc):
        rarity = m.group(1).lower()
        desc_mob = m.group(2)
        mob = mob_from_fname if mob_from_fname else desc_mob
        return SpawnEvent(mob=mob, rarity=rarity, **shared)

    # 2. Standard kill
    if m := _KILL_RE.match(desc):
        rarity = m.group(1).lower()
        desc_mob = m.group(2)
        killers = parse_players(m.group(3))
        mob = mob_from_fname if mob_from_fname else desc_mob
        return KillEvent(
            mob=mob, rarity=rarity, killers=killers, **shared,
        )

    # 3. Crafted / forged petal -> ignore (e.g. "The Unique Cactus has been
    #    forged by Chzhou66!" -- the petal Cactus shares the mob's name and
    #    image naming). See _CRAFTISH_RE for why this must be a wide net.
    if _CRAFTISH_RE.search(desc):
        return None

    # 4. Flavor-text spawn (no standard description pattern) -> mob + rarity
    #    both from the filename. This handles Rock/Cactus/Gambler/Jellyfish/
    #    Firefly/Hornet (any rarity) and Hel mobs (whose description is
    #    always flavor text "You sense ominous vibrations...").
    if mob_from_fname and _rarity_from_fname:
        return SpawnEvent(
            mob=mob_from_fname, rarity=_rarity_from_fname, **shared,
        )

    return None
