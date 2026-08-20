# MusicBrainz DB-only mirror (server03)

Feeds the deep-cuts side of `popularity_variety`: rose-cinema queries this
mirror's Postgres directly (artist MBIDs + discographies) via
`MUSICBRAINZ_DB_URL`. With the var unset, everything still works — the
slider just degrades to prompt-only.

Unlike the other stacks in `deploy/`, this one is **not** a compose file in
this repo and is **not** Portainer-managed: [musicbrainz-docker]
(https://github.com/metabrainz/musicbrainz-docker) ships its own repo with
`admin/configure` tooling that assembles the compose project, so it runs as
a plain `docker compose` project cloned on the server.

Sizing (DB-only, no Solr search): ~100 GB on disk plus a transient ~55 GB
dump download; 4 GB RAM floor. server03 (466 GB disk / 62 GB RAM) fits
comfortably. The full search-indexed variant needs ~350 GB — do not use it.

## One-time setup

```bash
ssh server03.local
sudo git clone https://github.com/metabrainz/musicbrainz-docker \
    /opt/containers/musicbrainz-docker
cd /opt/containers/musicbrainz-docker

# DB-only mirror profile: postgres + import/replication tooling, no web UI,
# no Solr.
sudo admin/configure with alt-db-only-mirror

# .env / local compose overrides:
#  - publish Postgres on 5433 (rose-cinema's own PG owns 5432)
#  - set a real postgres password
#  - set MUSICBRAINZ_REPLICATION_TOKEN (free: https://metabrainz.org/supporters/account-type)
sudo admin/configure add publishing-db-port   # then edit the port to 5433 if needed

df -h /   # confirm ≥160 GB free before importing

# Import the latest data dump. Expect 4-12 hours; run it in tmux overnight.
sudo docker compose up -d db
sudo docker compose run --rm musicbrainz createdb.sh -fetch

# Hourly replication packets:
sudo admin/configure add replication-cron
sudo docker compose up -d
```

## Read-only role for rose-cinema

```sql
-- psql as postgres on the mirror (port 5433)
CREATE ROLE rose_ro LOGIN PASSWORD '<choose>';
GRANT USAGE ON SCHEMA musicbrainz TO rose_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA musicbrainz TO rose_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA musicbrainz GRANT SELECT ON TABLES TO rose_ro;
```

rose-cinema's queries schema-qualify every table (`musicbrainz.artist` …),
so no `search_path` setup is needed.

## Wire up rose-cinema

Add to the rose-cinema stack env (Portainer → stack → env vars; remember
git redeploys wipe env vars, so also record it in `.keys/portainer-env.json`):

```
MUSICBRAINZ_DB_URL=postgresql+asyncpg://rose_ro:<pw>@host.docker.internal:5433/musicbrainz_db
```

Sanity check after import:

```bash
docker compose exec db psql -U musicbrainz musicbrainz_db -c \
  "SELECT count(*) FROM musicbrainz.artist;"
```

## Upkeep

- Replication applies hourly packets; if the mirror falls behind or the
  annual MusicBrainz schema upgrade lands, follow musicbrainz-docker's
  upgrade notes. rose-cinema tolerates a down/broken mirror (logs loudly,
  skips deep cuts).
- Delete the downloaded dump archives after a successful import to free
  ~55 GB.
