"""florr.io mob registry + filename parser.

Sources (cross-checked):
  - ~/Desktop/florr/better_en/mobs.txt  (full mob names)
  - ~/Desktop/florr/better_zh/mobs.txt  (Chinese display + pinyin keys)
  - User-provided Discord embed samples (2026-08)

Rarities observed in #game broadcasts:
  - Super   (green border,   ~hsla 154,100%,58.4%,1)
  - Unique  (dark gray,      hsla 0,0%,33.3%,1)
  - Eternal (light gray,     hsla 0,0%,93.3%,1)

Filename pattern:
  petal-<mob_base>-<rarity>[-x].png
  e.g. petal-wasp-super.png, petal-ghost-unique.png,
       petal-mecha_flower-eternal-x.png

The "-x" on kill embeds is a Discord decoration; it does NOT carry variant
information. The variant (e.g. "Mecha Wasp" vs "Wasp") is conveyed by the
mob name in the embed description, and the mob_base encodes the variant
(e.g. wasp_mecha -> "Mecha Wasp").

Hel mobs (Hel Beetle, Hel Wasp, Hel Spider, Hel Centipede) are treated as
STANDALONE mobs, not variants of the base mob. Rationale: a "variant" in
florr.io denotes the same creature with a different appearance (Mecha,
Dark, Shiny, ...), while Hel denotes a creature from a different realm --
a categorically different entity. base_mob("Hel Spider") therefore returns
"Hel Spider" (itself). The side benefit: the predictor's candidate set for
e.g. "Spider" naturally contains only {Spider, Mecha Spider} (Hel Spider
is its own base and is not included), and predict("Hel Spider", ...) just
trivially returns 100% Hel Spider -- no special-case "deterministic" handling
needed.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Filename base -> human display name
# ---------------------------------------------------------------------------

MOBS: dict[str, str] = {
    # All entries from the game translation file mobs.txt (better_en), except
    # body segments (centipede_*_body, leech_body) and worm_guts: those are
    # visuals/internal states of other mobs, never independent broadcast
    # subjects. Sorted alphabetically by key.
    "ant_baby":          "Baby Ant",
    "ant_egg":           "Ant Egg",
    "ant_hole":          "Ant Hole",
    "ant_queen":         "Queen Ant",
    "ant_soldier":       "Soldier Ant",
    "ant_soldier_diver": "Soldier Diver Ant",
    "ant_worker":        "Worker Ant",
    "assembler":         "Assembler",
    "barrel":            "Barrel",
    "bee":               "Bee",
    "beetle":            "Beetle",
    "beetle_hel":        "Hel Beetle",
    "beetle_mummy":      "Mummy Beetle",
    "beetle_nazar":      "Nazar Beetle",
    "beetle_pharaoh":    "Pharaoh Beetle",
    "bubble":            "Bubble",
    "bumble_bee":        "Bumble Bee",
    "bush":              "Bush",
    "cactus":            "Cactus",
    "centipede":         "Centipede",
    "centipede_desert":  "Desert Centipede",
    "centipede_evil":    "Evil Centipede",
    "centipede_hel":     "Hel Centipede",
    "crab":              "Crab",
    "crab_mecha":        "Mecha Crab",
    "crystal":           "Crystal",
    "dandelion":         "Dandelion",
    "digger":            "Digger",
    "dummy":             "Target Dummy",
    "fire_ant_baby":     "Baby Fire Ant",
    "fire_ant_burrow":   "Fire Ant Burrow",
    "fire_ant_egg":      "Fire Ant Egg",
    "fire_ant_queen":    "Queen Fire Ant",
    "fire_ant_soldier":  "Soldier Fire Ant",
    "fire_ant_worker":   "Worker Fire Ant",
    "firefly":           "Firefly",
    "firefly_magic":     "Magic Firefly",
    "fly":               "Fly",
    "gambler":           "Gambler",
    "garbage":           "Garbage",
    "ghost":             "Ghost",
    "hornet":            "Hornet",
    "jellyfish":         "Jellyfish",
    "ladybug":           "Ladybug",
    "ladybug_dark":      "Dark Ladybug",
    "ladybug_shiny":     "Shiny Ladybug",
    "leafbug":           "Leafbug",
    "leafbug_shiny":     "Shiny Leafbug",
    "leech":             "Leech",
    "mantis":            "Mantis",
    "mecha_flower":      "Mecha Flower",
    "moth":              "Moth",
    "oracle":            "Oracle",
    "roach":             "Roach",
    "rock":              "Rock",
    "sandstorm":         "Sandstorm",
    "scorpion":          "Scorpion",
    "shell":             "Shell",
    "silverfish":        "Silverfish",
    "spider":            "Spider",
    "spider_hel":        "Hel Spider",
    "spider_mecha":      "Mecha Spider",
    "sponge":            "Sponge",
    "square":            "Square",
    "starfish":          "Starfish",
    "termite_baby":      "Baby Termite",
    "termite_egg":       "Termite Egg",
    "termite_mound":     "Termite Mound",
    "termite_overmind":  "Termite Overmind",
    "termite_soldier":   "Soldier Termite",
    "termite_worker":    "Worker Termite",
    "titan":             "Titan",
    "tomb":              "Tomb",
    "trader":            "Trader",
    "wasp":              "Wasp",
    "wasp_hel":          "Hel Wasp",
    "wasp_mecha":        "Mecha Wasp",
    "worm":              "Worm",
}

# ---------------------------------------------------------------------------
# Display name -> base mob (for variant prediction / 30-min respawn grouping).
# Cosmetic variants (Mecha/Dark/Shiny/...) map to their base. Hel mobs and
# other standalones (Soldier Termite, Worker Ant, ...) map to themselves
# (base_mob("Hel Spider") == "Hel Spider").
# ---------------------------------------------------------------------------
BASE_OF: dict[str, str] = {
    "Mummy Beetle":      "Beetle",
    "Pharaoh Beetle":    "Beetle",
    "Nazar Beetle":      "Beetle",
    "Mecha Spider":      "Spider",
    "Mecha Wasp":        "Wasp",
    "Mecha Crab":        "Crab",
    "Dark Ladybug":      "Ladybug",
    "Shiny Ladybug":     "Ladybug",
    "Shiny Leafbug":     "Leafbug",
    "Evil Centipede":    "Centipede",
    "Desert Centipede":  "Centipede",
    "Soldier Diver Ant": "Soldier Ant",
    "Magic Firefly":     "Firefly",
}

# ---------------------------------------------------------------------------
# Rarities
# ---------------------------------------------------------------------------
RARITIES: tuple[str, ...] = ("super", "unique", "eternal")


def parse_filename(fname: str) -> tuple[str | None, str | None]:
    """Return (mob_base, rarity) extracted from a thumbnail filename.

    mob_base: e.g. "wasp", "wasp_mecha", "beetle_hel", "rock", "ghost"
    rarity    : one of "super"/"unique"/"eternal", or None if not recognized

    Returns (None, None) if the filename doesn't match the expected pattern
    or no known rarity is found.

    Examples:
        "petal-wasp-super.png"               -> ("wasp", "super")
        "petal-wasp_mecha-super-x.png"       -> ("wasp_mecha", "super")
        "petal-ghost-unique.png"             -> ("ghost", "unique")
        "petal-mecha_flower-eternal-x.png"   -> ("mecha_flower", "eternal")
        "petal-rock-unique.png"              -> ("rock", "unique")
        "petal-beetle_hel-eternal.png"       -> ("beetle_hel", "eternal")
        "petal-centipede_body-super.png"     -> ("centipede_body", "super")
        "petal-bee-super.png"                -> ("bee", "super")
    """
    if not fname:
        return (None, None)
    base = fname.split("?", 1)[0]                # strip CDN query string
    if not base.startswith("petal-") or not base.endswith(".png"):
        return (None, None)
    core = base[len("petal-"):-len(".png")]      # e.g. "wasp-super", "wasp_mecha-super-x"
    for rarity in RARITIES:
        suffix = f"-{rarity}"                      # "-super" / "-unique" / "-eternal"
        if core.endswith(suffix):
            return (core[: -len(suffix)], rarity)
        kill_suffix = suffix + "-x"
        if core.endswith(kill_suffix):
            return (core[: -len(kill_suffix)], rarity)
    return (None, None)


def resolve_mob_from_filename(fname: str) -> str | None:
    """Convenience: return just the display name (rarity ignored).

    "petal-wasp-super.png"              -> "Wasp"
    "petal-wasp_mecha-super-x.png"      -> "Mecha Wasp"
    "petal-beetle_hel-super.png"        -> "Hel Beetle"
    "petal-termite_soldier-super.png"   -> "Soldier Termite"
    "petal-rock-unique.png"             -> "Rock"
    "petal-ghost-unique.png"            -> "Ghost"
    "petal-mecha_flower-eternal-x.png"  -> "Mecha Flower"
    """
    base, _ = parse_filename(fname)
    return MOBS.get(base) if base else None


def base_mob(display_name: str) -> str:
    """Return the base mob for a (possibly variant) display name.

    "Mecha Wasp"       -> "Wasp"           (cosmetic variant -> its base)
    "Hel Spider"       -> "Hel Spider"     (Hel is a standalone mob, not a variant)
    "Soldier Termite"  -> "Soldier Termite" (standalone, maps to itself)
    "Wasp"             -> "Wasp"
    """
    return BASE_OF.get(display_name, display_name)

# ---------------------------------------------------------------------------
# Regions (servers) and shorthand resolution
# ---------------------------------------------------------------------------
_REGION_ALIASES: dict[str, str] = {
    "asia": "Sierra (ASIA)",
    "eu":   "Romeo (EU)",
    "us":   "Juliett (US)",
}


def resolve_region(arg: str) -> str | None:
    """Resolve a CLI region argument. ONLY the three shorthands are
    accepted: 'asia' / 'eu' / 'us' (case-insensitive). Nothing else.

    "EU"   -> "Romeo (EU)"
    "eu"   -> "Romeo (EU)"
    "junk" -> None
    """
    return _REGION_ALIASES.get(arg.strip().lower())
