"""Parser tests: rarity recognition, standard + flavor spawns, craft,
and the CRITICAL behavior that the THUMBNAIL FILENAME is authoritative
for the mob name (including variant)."""
from __future__ import annotations
from florr_notify.parser import (
    KillEvent,
    SpawnEvent,
    parse_embed,
    parse_players,
)
from florr_notify.mobs import (
    MOBS,
    base_mob,
    parse_filename,
    resolve_mob_from_filename,
)

SUPER_GREEN   = 0x2BFFA4
UNIQUE_GRAY   = 0x555555
ETERNAL_LIGHT = 0xEEEEEE

CID = 1349166028126556160
GID = 668937237010055189


# --- Real samples (user-provided HTML) ---

WASP_SPAWN_EMBED = {
    "type": "rich",
    "description": "\u200B\u200BA Super Wasp has spawned!",
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1547250033714266142/petal-wasp-super.png?ex=6aa2bc62&is=6aa16ae2&hm=40b264e5c9213175135c2f3c25bd0c7a11c45d5deec7c53553d7d6d465bb663b&"},
    "footer": {"text": "Romeo (EU)"},
}

STARFISH_KILL_EMBED = {
    "type": "rich",
    "description": "\u200BA Super Starfish has been defeated by SOYA, _NMRN_, wwwwwww and Aleksey!",
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1547250348186407103/petal-starfish-super-x.png?ex=6aa2bcad&is=6aa16b2d&hm=7d1a7f6a418c38c47720787dc62fcba980a569fd258ed70ee93dd00b973297d3&"},
    "footer": {"text": "Sierra (ASIA)"},
}

TERMITE_SPAWN_EMBED = {
    "type": "rich",
    "description": "\u200B\u200BA Super Soldier Termite has spawned!",
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1547252560614793216/petal-termite_soldier-super.png?ex=6aa2bebd&is=6aa16d3d&hm=21b5689ed0f7e3541d41146d2e79769d6246f688199277e363471c4d568b5c99&"},
    "footer": {"text": "Sierra (ASIA)"},
}

TERMITE_KILL_EMBED = {
    "type": "rich",
    "description": (
        "\u200BA Super Soldier Termite has been defeated by 1217king, "
        "bingole, ToMaaaaaaaaaa and gainer!"
    ),
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1547252748582387762/petal-termite_soldier-super-x.png?ex=6aa2bee9&is=6aa16d69&hm=423d59badc30ca8f6e22ed71827ac3bf342ee44310da0107e0c7555710c54705&"},
    "footer": {"text": "Sierra (ASIA)"},
}

HEL_SPIDER_SPAWN_EMBED = {
    "type": "rich",
    "description": "\u200B\u200BYou sense ominous vibrations coming from a different realm...",
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1541778977184817243/petal-spider_hel-super.png?ex=6aa29b91&is=6aa14a11&hm=51f4180f34e58322bf74ccf4af868422282530a66a4a4a635a339d287a92bd2e&"},
    "footer": {"text": "Juliett (US)"},
}

CRAFT_EMBED = {
    "type": "rich",
    "description": "A Super Electric Web has been crafted by 323521!",
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1547219837921792070/petal-electric_web-super.png?ex=6aa2a043&is=6aa14ec3&hm=fdc8cf929251b9c02211b2c2dfeed740ad642d3630d14b457ee34062c242ed13&"},
    "footer": {"text": "Sierra (ASIA)"},
}

UNIQUE_GHOST_SPAWN_EMBED = {
    "type": "rich",
    "description": "\u200B\u200BA Unique Ghost has spawned!",
    "color": UNIQUE_GRAY,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1443239363894054932/petal-ghost-unique.png?ex=6aa2b766&is=6aa165e6&hm=34c89a8b303e72bcfe3304fae40af4f851aaf85362e142f4ca750594008c6a42&"},
    "footer": {"text": "Sierra (ASIA)"},
}

ETERNAL_MECHA_FLOWER_KILL_EMBED = {
    "type": "rich",
    "description": "An Eternal Mecha Flower has been defeated by gonee_!",
    "color": ETERNAL_LIGHT,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1488746712242720829/petal-mecha_flower-eternal-x.png?ex=6aa2286b&is=6aa0d6eb&hm=4bff4ccb546dd29206ea3c53d15f78dbf9d8e30be9033959ea8e913982d4b1e1&"},
    "footer": {"text": "Romeo (EU)"},
}

# *** THE KEY SAMPLE: broadcast text says "Spider" (base), image is Mecha
# Spider. The parser MUST use the filename (the authoritative source) to
# report mob="Mecha Spider", not mob="Spider".
MECHA_SPIDER_KILL_EMBED = {
    "type": "rich",
    "description": "A Super Spider has been defeated by Luai2, mob2, XIAO8 and wcnmlgbd!",
    "color": SUPER_GREEN,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/1349166028126556160/1547269314606792754/petal-spider_mecha-super-x.png?ex=6aa2ce57&is=6aa17cd7&hm=b916048212c5559a87c4c62963ed5df6bcc20887440f3099579e84df0cfd066f&"},
    "footer": {"text": "Sierra (ASIA)"},
}

# Synthetic: Unique Rock flavor spawn
UNIQUE_ROCK_SPAWN_EMBED = {
    "type": "rich",
    "description": "Something mountain-like appears in the distance...",
    "color": UNIQUE_GRAY,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/X/petal-rock-unique.png"},
    "footer": {"text": "Sierra (ASIA)"},
}


def _msg_args(message_id: int = 1, observed_at: str = "2026-08-09T00:00:00"):
    return dict(message_id=message_id, channel_id=CID, guild_id=GID, observed_at=observed_at)


# --- Real-sample tests ---

def test_parse_wasp_spawn():
    e = parse_embed(WASP_SPAWN_EMBED, **_msg_args())
    assert isinstance(e, SpawnEvent)
    assert e.mob == "Wasp" and e.rarity == "super"
    assert e.image_url.startswith("https://cdn.discordapp.com/")


def test_parse_starfish_kill():
    e = parse_embed(STARFISH_KILL_EMBED, **_msg_args(message_id=2))
    assert isinstance(e, KillEvent)
    assert e.mob == "Starfish" and e.rarity == "super"
    assert e.killers == ["SOYA", "_NMRN_", "wwwwwww", "Aleksey"]


def test_parse_soldier_termite_paired():
    s = parse_embed(TERMITE_SPAWN_EMBED, **_msg_args(message_id=42))
    k = parse_embed(TERMITE_KILL_EMBED, **_msg_args(message_id=42, observed_at="t1"))
    assert s.mob == "Soldier Termite" and k.mob == "Soldier Termite"
    assert k.killers == ["1217king", "bingole", "ToMaaaaaaaaaa", "gainer"]


def test_parse_hel_spider_flavor_spawn():
    e = parse_embed(HEL_SPIDER_SPAWN_EMBED, **_msg_args(message_id=100))
    assert e.mob == "Hel Spider" and e.rarity == "super"


def test_parse_unique_ghost_spawn():
    e = parse_embed(UNIQUE_GHOST_SPAWN_EMBED, **_msg_args(message_id=500))
    assert e.mob == "Ghost" and e.rarity == "unique"


def test_parse_eternal_mecha_flower_kill():
    e = parse_embed(ETERNAL_MECHA_FLOWER_KILL_EMBED, **_msg_args(message_id=600))
    assert e.mob == "Mecha Flower" and e.rarity == "eternal"
    assert e.killers == ["gonee_"]


def test_parse_unique_rock_flavor_spawn():
    e = parse_embed(UNIQUE_ROCK_SPAWN_EMBED, **_msg_args(message_id=700))
    assert e.mob == "Rock" and e.rarity == "unique"


def test_parse_mecha_spider_kill_uses_filename():
    """THE CRITICAL TEST: description says 'Spider' (base), but the image
    is the Mecha Spider variant. The parser MUST use the filename and
    report mob='Mecha Spider' (not 'Spider' from the description)."""
    e = parse_embed(MECHA_SPIDER_KILL_EMBED, **_msg_args(message_id=800))
    assert isinstance(e, KillEvent)
    assert e.mob == "Mecha Spider", (
        f"parser must use filename (spider_mecha -> Mecha Spider), got {e.mob!r}"
    )
    assert e.rarity == "super"
    assert e.killers == ["Luai2", "mob2", "XIAO8", "wcnmlgbd"]
    assert e.image_filename == "petal-spider_mecha-super-x.png"


def test_parse_craft_ignored():
    assert parse_embed(CRAFT_EMBED, **_msg_args(message_id=400)) is None


FORGED_PETAL_EMBED = {
    "type": "rich",
    "description": (
        "\u200b\n\u200bThe Unique Cactus has been forged by Chzhou66!"
    ),
    "color": 0,
    "thumbnail": {"url": "https://cdn.discordapp.com/attachments/x/petal-cactus-unique.png"},
    "footer": {"text": ""},
}


def test_parse_forged_petal_ignored_even_when_name_collides_with_mob():
    """Regression: the petal Cactus shares name AND image naming with the
    mob Cactus. 'forged by' (not 'crafted by') slipped past the craft check
    and the flavor-spawn fallback recorded a fake Unique Cactus spawn
    (whitelist-exempt -> desktop notification). Any 'has been <verb> by'
    that is not a kill must be ignored."""
    assert parse_embed(FORGED_PETAL_EMBED, **_msg_args(message_id=401)) is None


# --- Edge cases ---

def test_zwsp_stripped():
    emb = dict(WASP_SPAWN_EMBED, description="\uFEFF\u200B A Super Wasp has spawned! \u200B")
    assert parse_embed(emb, **_msg_args()).mob == "Wasp"


def test_non_matching_returns_none():
    emb = {"type": "rich", "description": "Hello world", "color": 0,
           "thumbnail": {"url": ""}, "footer": {"text": ""}}
    assert parse_embed(emb, **_msg_args()) is None


def test_no_description_returns_none():
    assert parse_embed({"type": "rich", "description": None}, **_msg_args()) is None


def test_empty_filename_falls_back_to_description():
    """Unknown mob (e.g. a new one not in MOBS): filename lookup fails, fall
    back to the description's mob name. (Not ideal, but safe.)"""
    emb = {
        "type": "rich",
        "description": "A Super NewMob has spawned!",
        "color": 0,
        "thumbnail": {"url": "https://x/petal-newmob-super.png"},  # newmob not in MOBS
        "footer": {"text": "Romeo (EU)"},
    }
    e = parse_embed(emb, **_msg_args())
    assert e.mob == "NewMob"  # falls back to description
    assert e.rarity == "super"


# --- Helpers ---

def test_parse_players():
    assert parse_players("A") == ["A"]
    assert parse_players("A and B") == ["A", "B"]
    assert parse_players("A, B and C") == ["A", "B", "C"]
    assert parse_players("SOYA, _NMRN_, wwwwwww and Aleksey") == [
        "SOYA", "_NMRN_", "wwwwwww", "Aleksey",
    ]
    assert parse_players("1217king, bingole, ToMaaaaaaaaaa and gainer") == [
        "1217king", "bingole", "ToMaaaaaaaaaa", "gainer",
    ]


# --- Mob registry + filename parser ---

def test_resolve_mob_from_filename():
    assert resolve_mob_from_filename("petal-wasp-super.png") == "Wasp"
    assert resolve_mob_from_filename("petal-wasp_mecha-super-x.png") == "Mecha Wasp"
    assert resolve_mob_from_filename("petal-spider_mecha-super-x.png") == "Mecha Spider"
    assert resolve_mob_from_filename("petal-beetle_hel-super.png") == "Hel Beetle"
    assert resolve_mob_from_filename("petal-termite_soldier-super.png") == "Soldier Termite"
    assert resolve_mob_from_filename("petal-rock-super.png") == "Rock"
    assert resolve_mob_from_filename("petal-centipede_evil-super-x.png") == "Evil Centipede"
    assert resolve_mob_from_filename("petal-ghost-unique.png") == "Ghost"
    assert resolve_mob_from_filename("petal-mecha_flower-eternal-x.png") == "Mecha Flower"
    assert resolve_mob_from_filename("petal-centipede_body-super.png") is None
    assert resolve_mob_from_filename("petal-bee-super.png") is None
    assert resolve_mob_from_filename("") is None
    assert resolve_mob_from_filename("not-a-filename") is None


def test_parse_filename_extracts_tier():
    assert parse_filename("petal-wasp-super.png") == ("wasp", "super")
    assert parse_filename("petal-wasp_mecha-super-x.png") == ("wasp_mecha", "super")
    assert parse_filename("petal-spider_mecha-super-x.png") == ("spider_mecha", "super")
    assert parse_filename("petal-ghost-unique.png") == ("ghost", "unique")
    assert parse_filename("petal-mecha_flower-eternal-x.png") == ("mecha_flower", "eternal")
    assert parse_filename("petal-rock-unique.png") == ("rock", "unique")
    assert parse_filename("petal-wasp-super.png?ex=foo") == ("wasp", "super")
    assert parse_filename("petal-bee-legendary.png") == (None, None)
    assert parse_filename("") == (None, None)


def test_base_mob_lookup():
    assert base_mob("Mecha Spider") == "Spider"
    assert base_mob("Hel Spider") == "Hel Spider"   # Hel mobs are standalone
    assert base_mob("Mecha Wasp") == "Wasp"
    assert base_mob("Mummy Beetle") == "Beetle"
    assert base_mob("Soldier Diver Ant") == "Soldier Ant"
    assert base_mob("Wasp") == "Wasp"
    assert base_mob("Soldier Termite") == "Soldier Termite"
    assert base_mob("Mecha Flower") == "Mecha Flower"


def test_registry_covers_all_user_listed_variants():
    required = [
        ("Wasp", "Mecha Wasp"), ("Spider", "Mecha Spider"),
        ("Crab", "Mecha Crab"), ("Ladybug", "Dark Ladybug"),
        ("Ladybug", "Shiny Ladybug"), ("Leafbug", "Shiny Leafbug"),
        ("Centipede", "Evil Centipede"), ("Centipede", "Desert Centipede"),
        ("Beetle", "Mummy Beetle"), ("Beetle", "Pharaoh Beetle"),
        ("Beetle", "Nazar Beetle"), ("Soldier Ant", "Soldier Diver Ant"),
        ("Firefly", "Magic Firefly"),
        # Hel mobs are standalone; they are not variants of the base, so
        # they are intentionally absent from BASE_OF and from this list.
    ]
    for base, variant in required:
        assert base_mob(variant) == base
        assert variant in MOBS.values() or variant == base
