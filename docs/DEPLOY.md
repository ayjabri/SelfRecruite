# Running SelfRecruite as a service (native install)

The Docker Compose setup in the README is the simplest way to run the stack. This page is for a
native install on a Linux server: the app in its own Python environment, Postgres in the `db`
container, a systemd unit for the page, timers for the crawl and the backup, and Caddy for HTTPS.

## systemd units

The unit files in `systemd/` carry three placeholders: `__INSTALL_DIR__` (the checkout),
`__BIN_DIR__` (the `bin` directory of the Python environment that has the project installed) and
`__USER__` (the account that owns the checkout). Fill them in and install:

```bash
INSTALL_DIR=$(pwd); BIN_DIR=$(dirname "$(command -v jobagent)"); RUN_AS=$(id -un)
for u in systemd/*; do
  sed -e "s#__INSTALL_DIR__#$INSTALL_DIR#g" -e "s#__BIN_DIR__#$BIN_DIR#g" -e "s#__USER__#$RUN_AS#g" "$u" \
    | sudo tee "/etc/systemd/system/$(basename "$u")" > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now jobagent-ui.service jobagent-crawl.timer jobagent-backup.timer
```

- `jobagent-ui.service` runs the page on `127.0.0.1:8765` behind a reverse proxy. Python changes
  need `sudo systemctl restart jobagent-ui.service`; page-only changes need just a browser reload.
- `jobagent-crawl.timer` runs the daily pull at 06:00.
- `jobagent-backup.timer` runs `scripts/backup_db.sh` at 02:30: a `pg_dump` into `backups/`,
  14 days kept, optionally copied off-box with `BACKUP_REMOTE` in `.env`.

## HTTPS

`deploy/Caddyfile` serves the page over HTTPS with Caddy's internal certificate authority on a
LAN, or with automatic public certificates when you give it a domain. [HTTPS.md](HTTPS.md) walks
through installing the root certificate on each device. Set `COOKIE_SECURE=true` once HTTPS is on.

## Upgrading

```bash
git pull
pip install -e .[dev]
alembic upgrade head
sudo systemctl restart jobagent-ui.service
```
