# Docker setup

One image `loophedge:dev` runs every service; the `command:` field in
`docker-compose.yml` selects the CLI subcommand.

## Build only

```bash
docker compose build loophedge-base
```

## Run (developer's choice — not automated by plan execution)

```bash
docker compose up postgres redis dashboard data-ingestor
```

Maker / checker / genesis agents are placeholders until Phase 2.
