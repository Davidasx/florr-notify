from __future__ import annotations
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .collector import Collector
from .notify import Notifier
from .store import Store
from .mobs import base_mob


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def cmd_run(cfg) -> None:
    _setup_logging(cfg.log_level)
    log = logging.getLogger("florr-notify")
    log.info("starting collector (channel=%s, db=%s)",
             cfg.discord.game_channel_id, cfg.storage.db_path)
    store = Store(cfg.storage.db_path)
    notifier = Notifier(cfg.notify)
    client = Collector(cfg, store, notifier)
    try:
        # dpy-self 2.x: self-bot only; run() takes the user token directly.
        client.run(cfg.discord.token)
    finally:
        store.close()


def cmd_status(cfg) -> None:
    store = Store(cfg.storage.db_path)
    try:
        total = store.count()
        print(f"incidents total: {total}")
        print("recent (latest first, up to 10):")
        for row in store.recent(10):
            print(
                f"  msg={row['message_id']}  rarity={(row['rarity'] or '?'):<7}  "
                f"mob={row['mob']!r}  base={base_mob(row['mob'])!r}  "
                f"region={row['region']!r}  "
                f"spawn_at={row['spawn_at']}  killed_at={row['killed_at']}  "
                f"edits={row['edit_count']}"
            )
    finally:
        store.close()


def cmd_predict(cfg, mob_name: str, server_arg: str | None = None) -> None:
    # Imported lazily so `florr-notify run` startup isn't slowed by it.
    from .mobs import resolve_region
    from .predictor import predict

    server = None
    if server_arg:
        server = resolve_region(server_arg)
        if server is None:
            print(
                f"Unknown region {server_arg!r}; only 'asia' / 'eu' / 'us' "
                f"are accepted."
            )
            return
    store = Store(cfg.storage.db_path)
    try:
        # server=None -> no cooldown exclusion; a resolved server applies
        # the 30-min per-server variant cooldown.
        print(predict(mob_name, store, server=server))
    finally:
        store.close()


def _fmt_age(ts: str, now: datetime) -> str:
    mins = int((now - datetime.fromisoformat(ts)).total_seconds() // 60)
    if mins < 60:
        return f"{mins}m ago"
    return f"{mins // 60}h{mins % 60:02d}m ago"


def _print_by_region(
    groups: dict[str, list[tuple[str, str]]], header: str,
) -> None:
    if not groups:
        print(f"{header}: none")
        return
    total = sum(len(v) for v in groups.values())
    print(f"{header}: {total}")
    for region in sorted(groups):
        print(f"  {region}:")
        for mob, detail in groups[region]:
            print(f"    {mob:<22} {detail}")


def cmd_alive(cfg) -> None:
    """Mobs spawned within the last 24h with no kill broadcast (believed
    alive), grouped by server, newest spawn first. For multi-variant mobs
    a variant prediction (distribution + confidence) is shown: the
    cooldown exclusion is evaluated at the mob's own spawn time, since
    its variant was fixed the moment it spawned."""
    from .mobs import base_mob as _base_mob
    from .predictor import predict, format_prediction_line
    now = datetime.now(timezone.utc)
    store = Store(cfg.storage.db_path)
    entries: list[tuple[str, str, str, str | None]] = []
    try:
        rows = store.alive()
        for r in rows:
            spawn_dt = datetime.fromisoformat(r["spawn_at"])
            pred_line = None
            try:
                pred = predict(
                    _base_mob(r["mob"]), store,
                    server=r["region"], now=spawn_dt,
                )
                pred_line = format_prediction_line(pred)
            except Exception as exc:   # prediction is best-effort
                print(f"(prediction failed for {r['mob']!r}: {exc})",
                      file=sys.stderr)
            entries.append((r["region"], r["mob"],
                            _fmt_age(r["spawn_at"], now), pred_line))
    finally:
        store.close()
    groups: dict[str, list[tuple[str, str, str | None]]] = {}
    for region, mob, age, pred_line in entries:
        groups.setdefault(region, []).append((mob, age, pred_line))
    if not groups:
        print("alive mobs (spawned, no kill broadcast): none")
        return
    total = sum(len(v) for v in groups.values())
    print(f"alive mobs (spawned, no kill broadcast): {total}")
    for region in sorted(groups):
        print(f"  {region}:")
        for mob, age, pred_line in groups[region]:
            print(f"    {mob:<22} spawned {age}")
            if pred_line:
                print(f"      {pred_line}")


def cmd_cooldown(cfg) -> None:
    """Mobs in the 30-min post-kill respawn cooldown, grouped by server,
    soonest to expire first. Both base mobs and variants are shown (the
    cooldown applies to the exact variant per server)."""
    from .predictor import COOLDOWN
    store = Store(cfg.storage.db_path)
    try:
        rows = store.recent_kills()
    finally:
        store.close()
    now = datetime.now(timezone.utc)
    groups: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        remaining = COOLDOWN - (now - datetime.fromisoformat(r["killed_at"]))
        if remaining.total_seconds() <= 0:
            continue
        mins = remaining.total_seconds() / 60
        groups.setdefault(r["region"], []).append(
            (r["mob"], f"{mins:5.1f}m left"),
        )
    for g in groups.values():
        g.sort(key=lambda t: t[1])
    _print_by_region(groups, "mobs in respawn cooldown")

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="florr-notify",
        description="Local florr.io Super mob Discord monitor (read-only).",
    )
    ap.add_argument(
        "-c", "--config", default="config.toml",
        help="Path to config.toml (default: ./config.toml)",
    )
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Run the collector (foreground daemon)")
    p_run.set_defaults(_fn=cmd_run)

    p_status = sub.add_parser("status", help="Show DB summary + recent incidents")
    p_status.set_defaults(_fn=cmd_status)

    p_predict = sub.add_parser(
        "predict", help="Predict next variant for a base mob from history"
    )
    p_predict.add_argument(
        "mob",
        help="Base mob display name, e.g. 'Wasp', 'Beetle', 'Ladybug'",
    )
    p_predict.add_argument(
        "server", nargs="?", default=None,
        help="Server shorthand: only 'asia' / 'eu' / 'us'; enables the "
             "30-min cooldown exclusion (e.g. predict leafbug eu)",
    )

    p_alive = sub.add_parser(
        "alive", help="Mobs believed alive, grouped by server"
    )
    p_alive.set_defaults(_fn=cmd_alive)

    p_cooldown = sub.add_parser(
        "cooldown", help="Mobs in the 30-min respawn cooldown, by server"
    )
    p_cooldown.set_defaults(_fn=cmd_cooldown)
    p_predict.set_defaults(_fn=cmd_predict)

    args = ap.parse_args(argv)
    cfg = load_config(Path(args.config))

    if args.cmd is None:
        # No subcommand -> default to run
        cmd_run(cfg)
        return 0

    if args.cmd == "predict":
        cmd_predict(cfg, args.mob, args.server)
    else:
        args._fn(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
