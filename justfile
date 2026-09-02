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

# Synthesize the AWS stacks without deploying. Needs no credentials.
infra-synth:
    npm --prefix infra install
    npm --prefix infra run synth -- --quiet

# Deploy the API stack. Needs AWS credentials and Docker.
infra-deploy:
    npm --prefix infra install
    npm --prefix infra run deploy

# Regenerate the console's TypeScript types from the API's OpenAPI document.
types:
    uv run worth-api-openapi > app/src/api/openapi.json
    npm --prefix app run types

# The backend, in development, against the compose Postgres. http://localhost:8000/api/docs
api-dev: db-up
    DATABASE_URL=postgresql://worth:worth@127.0.0.1:55432/worth \
      uv run uvicorn worth_api.main:create_app --factory --reload --reload-dir packages --port 8000

# The backend without a database: prices from the committed fixture.
api-dev-offline:
    uv run uvicorn worth_api.main:create_app --factory --reload --reload-dir packages --port 8000

# The tests that need Postgres, against the compose database.
test-db: db-up
    DATABASE_URL=postgresql://worth:worth@127.0.0.1:55432/worth uv run pytest -q -m db

# Backend and Postgres together, in containers. http://localhost:8000/api/health
up:
    docker compose up -d --build --wait

down:
    docker compose down

# The console, in development. http://localhost:5173, proxies /api to :8000
app-dev:
    npm --prefix app install
    npm --prefix app run dev

# Production build of the console into app/dist. Writes files; serves nothing.
app-build:
    npm --prefix app ci
    npm --prefix app run build

# Build, then serve app/dist exactly as Amplify will. http://localhost:4173
app-preview: app-build
    npm --prefix app run preview -- --port 4173 --strictPort

# Rebuild the synthetic partner dataset. Second argument scales the cohort.
build-dataset DIR="packages/worth-complexity/worth_complexity/fixtures/mssm-synthetic" SCALE="1":
    uv run python packages/worth-complexity/tools/make_synthetic_dataset.py {{DIR}} {{SCALE}}

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
