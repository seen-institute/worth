# WORTH task runner. `just --list` for everything.

default:
    @just --list

# One allowed amount, its derivation trace, and the source hash. Offline.
demo *ARGS:
    uv run worth-fees demo {{ARGS}}

# Price one code: just price 99213 CA18 2026-03-14
price CODE PLACE DATE *ARGS:
    uv run worth-fees price {{CODE}} {{PLACE}} {{DATE}} {{ARGS}}

# Layer A scores, Method 0 slopes and adequacy ratios on the synthetic dataset.
complexity-demo *ARGS:
    uv run worth-complexity demo {{ARGS}}

# One encounter's complexity derivation and its adequacy arithmetic.
complexity-encounter ID *ARGS:
    uv run worth-complexity encounter {{ID}} {{ARGS}}

# Every study encounter: score, operative minutes, realized payment, ratio.
complexity-cases *ARGS:
    uv run worth-complexity cases {{ARGS}}

# Rebuild a synthetic partner dataset. CLASS picks surgical|visit|episode|mixed
# (CONTRACT-PACKS.md / CONTRACT-PACKS-MC.md); DIR defaults to that class's own
# fixture directory. SCALE scales the cohort. The visit and episode generators
# are agent V's and E's (`tools/make_visit_dataset.py`,
# `tools/make_episode_dataset.py`); `mixed` (agent MC) composes all three via
# `tools/make_mixed_dataset.py` into `fixtures/mixed-synthetic/` without
# regenerating any of the three single-class fixtures.
build-dataset CLASS="surgical" DIR="" SCALE="1":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{CLASS}}" in
      surgical)
        script=packages/worth-complexity/tools/make_synthetic_dataset.py
        default_dir=packages/worth-complexity/worth_complexity/fixtures/mssm-synthetic
        ;;
      visit)
        script=packages/worth-complexity/tools/make_visit_dataset.py
        default_dir=packages/worth-complexity/worth_complexity/fixtures/visit-synthetic
        ;;
      episode)
        script=packages/worth-complexity/tools/make_episode_dataset.py
        default_dir=packages/worth-complexity/worth_complexity/fixtures/episode-synthetic
        ;;
      mixed)
        script=packages/worth-complexity/tools/make_mixed_dataset.py
        default_dir=packages/worth-complexity/worth_complexity/fixtures/mixed-synthetic
        ;;
      *)
        echo "build-dataset: unknown CLASS '{{CLASS}}' (want surgical|visit|episode|mixed)" >&2
        exit 1
        ;;
    esac
    dir="{{DIR}}"
    if [ -z "$dir" ]; then dir="$default_dir"; fi
    uv run python "$script" "$dir" {{SCALE}}

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

# The tests that need Postgres, against the compose database.
test-db: db-up
    DATABASE_URL=postgresql://worth:worth@127.0.0.1:55432/worth uv run pytest -q -m db

# Start local Postgres only. The schema is applied on first boot.
db-up:
    docker compose up -d --wait db
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
