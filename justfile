# WORTH — task runner. `just --list` for everything.

default:
    @just --list

# One allowed amount, its derivation trace, and the source hash. Offline.
demo *ARGS:
    uv run worth-fees demo {{ARGS}}

# Price one code: just price 99213 CA18 2026-03-14
price CODE PLACE DATE *ARGS:
    uv run worth-fees price {{CODE}} {{PLACE}} {{DATE}} {{ARGS}}

test:
    uv run pytest

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy

# Apply the fixes `just lint` would only report.
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Print computed amounts to check by hand against the CMS Look-Up Tool.
verify-rates:
    uv run worth-fees verify-rates

# Re-download the pinned CMS release, verify its hash, rebuild the fixture.
refresh-fixture:
    uv run worth-fees build-fixture

vintages:
    uv run worth-fees vintages

# Emit the Postgres load script. Add --full for all ~19k rows (needs the cache).
export-sql *ARGS:
    uv run worth-fees export-sql {{ARGS}}

# Start local Postgres. The schema is applied on first boot.
db-up:
    docker compose up -d --wait
    @echo "postgres://worth:worth@127.0.0.1:55432/worth"

# Load a vintage (--full for all ~19k rows). Uses psql inside the container.
db-load *ARGS:
    uv run worth-fees export-sql {{ARGS}} \
      | docker compose exec -T db psql -U worth -d worth -v ON_ERROR_STOP=1 --quiet
    @echo "loaded. try: SELECT * FROM allowed_amount LIMIT 20;"

# Open a psql shell in the container.
db-shell:
    docker compose exec db psql -U worth -d worth

db-down:
    docker compose down

# Destroy the volume and rebuild from an empty database.
db-reset:
    docker compose down -v
    just db-up
