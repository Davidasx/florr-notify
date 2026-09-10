# florr-notify

A local, read-only Discord monitor for the florr.io server. It watches the
`#game` channel for Super/Unique/Eternal mob spawn and kill broadcasts, stores
them in SQLite, sends desktop notifications (with mob icons), and predicts the
variant of newly spawned multi-variant mobs (frequency analysis + the
30-minute respawn cooldown).

## Architecture

```
Discord #game ──(gateway WebSocket)──▶ discord.py-self Collector
                                            │
                                  parse_embed (spawn / kill)
                                            │
                              ┌─────────────┼─────────────┐
                              ▼             ▼             ▼
                          Store        Notifier      Predictor
                          (SQLite)   (notify-send)  (variant estimate)
```

## Install

Requires Python ≥ 3.10 (3.12 recommended; `curl_cffi` ships prebuilt wheels
for 3.12/3.13).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

## Configuration

1. Extract your token (see how the parser authenticates: DevTools → Network →
   the `Authorization` header of any Discord request). **Using a burner
   account is strongly recommended.**
2. Copy the example config:
   ```bash
   cp config.example.toml config.toml
   chmod 600 config.toml
   # edit config.toml and fill in your token
   ```

## Running

Development mode:

```bash
florr-notify run         # foreground
florr-notify status      # DB summary (last 10 rows)
florr-notify alive       # mobs believed alive, with variant predictions
florr-notify cooldown    # per-server 30-min respawn cooldowns
florr-notify predict <mob> [server]   # variant prediction for the next spawn
```

systemd user service (recommended for long-running use) — the unit file is
not part of the repo; write it directly to your user directory:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/florr-notify.service <<'EOF'
[Unit]
Description=florr.io Discord monitor (collector + desktop notifier)
After=network-online.target
# give up after 5 consecutive failures within 10 minutes (e.g. revoked token)
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
WorkingDirectory=/path/to/florr-notify
ExecStart=/path/to/florr-notify/.venv/bin/florr-notify run
# discord.py-self reconnects with exponential backoff on its own; Restart
# only covers process crashes and token revocation
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF
# replace /path/to/florr-notify with your actual install location
systemctl --user daemon-reload
systemctl --user enable --now florr-notify
journalctl --user -u florr-notify -f
```

## Behavior

- **Read-only**: never sends messages, never joins/leaves servers, calls no
  REST write endpoints.
- Gateway disconnects are resumed/reconnected automatically by dpy-self.
- Kill broadcasts are EDITS of the spawn message. They are handled via the
  raw edit event so they work even for messages outside the client's
  in-memory cache (dpy-self only dispatches the cached `message_edit` for
  known messages, and the cache is empty after every restart).
- On startup it backfills the **last 24 hours**, which:
  - sees kills that happened while offline (`edited_timestamp` is the real
    kill time);
  - marks mobs spawned within 24h with no kill broadcast as **alive**
    (list them with `florr-notify alive`);
  - is idempotent: processing the same message twice never duplicates rows.
- **No desktop notifications during backfill** (avoids a startup flood);
  data is stored silently.
- Duplicate kills (the same message edited repeatedly with identical text)
  are deduplicated by `raw_description`.
- Kill broadcast images are the authoritative variant source: the embed text
  only ever names the base mob, while the image filename reveals the actual
  variant (`petal-wasp_mecha-super-x.png` → Mecha Wasp). The spawn image
  always shows the base mob, so a spawned mob's variant is genuinely unknown
  until it dies.
- Notification filtering: `[whitelist]` in `config.toml` (separate spawn/kill
  lists; spawns match by base mob — variant entries are ignored — and kills
  match exactly) and `[notify] silent` (suppress all desktop notifications).
  Filtered events are still stored and logged in full. **Unique / Eternal
  broadcasts always notify**, whitelist notwithstanding.
- Variant prediction: spawn-time predictions use a KT-smoothed (α = 1/2)
  frequency estimate over the candidate variants, exclude variants currently
  inside the 30-minute respawn cooldown on that server (evaluated at the
  mob's own spawn time), and report a confidence = fraction of prior
  uncertainty resolved by the observed data (closed form, see
  `predictor.py`).

## Tests

```bash
source .venv/bin/activate
pytest -q
```

Coverage highlights:

- `test_parser.py`: real embed samples lock down spawn/kill parsing
  (Super Wasp / Super Starfish / Super Soldier Termite paired messages).
- `test_store.py`: spawn→kill state machine, variant reveal on kill,
  kill-overwrites-spawn backfill, dedup.
- `test_predictor.py`: KT estimates, cooldown exclusion (incl. per-server
  and expiry), confidence values, Hel-as-standalone invariant.

## Risks

Automating a user account violates the Discord ToS. Use a burner account,
stay read-only, and make sure the account has no active restrictions before
running long-term. If Discord ever sends you a security notice, stop
immediately:

```bash
systemctl --user stop florr-notify
```

## Security

- Never paste the real token into any website, chat, or online decoder.
- `config.toml` is gitignored; keep it that way.
