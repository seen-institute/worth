"""The HTTP surface. Routes only; the work happens in the other modules.

Everything lives under ``/api`` so the console can be served from the same
origin with one rewrite rule and no CORS. There is no authentication: the
dataset is synthetic, nothing can be uploaded, and the console is public.
Whatever grows an auth layer grows it here, in front of these routes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from worth_complexity.provenance import sha256_file
from worth_complexity.rulepack import load
from worth_complexity.version import __version__ as complexity_version
from worth_fees import WorthFeesError
from worth_fees import __version__ as fees_version

from worth_api import __version__ as api_version
from worth_api.dataset import (
    Dataset,
    DeliveredFile,
    UnknownFileError,
    archive,
    file_payload,
    manifest,
    raw_path,
)
from worth_api.export import RunResult, Scoring, note_engine, rule_pack
from worth_api.fees import Derivation, FeeService, Locality, PriceRequest, VintageInfo, derivation
from worth_api.runs import RunService
from worth_api.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path

PACK_NAME = "gyn-surgical-v1"


class RulePackRef(BaseModel):
    id: str
    digest: str
    status: str


class Health(BaseModel):
    """What this instance is, and what it is pointed at."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    api: str
    worth_complexity: str
    worth_fees: str
    rule_pack: RulePackRef
    dataset_dir: str
    locality: str
    setting: str
    database: str
    """``not configured``, or host, port and database name. Never the credentials."""
    fees: list[VintageInfo]
    latest_run: bool


def get_settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def get_runs(request: Request) -> RunService:
    r: RunService = request.app.state.runs
    return r


def get_fees(request: Request) -> FeeService:
    f: FeeService = request.app.state.fees
    return f


# Module level, not inside create_app: with postponed annotations FastAPI
# resolves a parameter's annotation string against this module's globals, and
# a local alias would silently become a query parameter.
SettingsDep = Annotated[Settings, Depends(get_settings)]
RunsDep = Annotated[RunService, Depends(get_runs)]
FeesDep = Annotated[FeeService, Depends(get_fees)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="worth-api",
        version=api_version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    fees = FeeService(settings)
    # Boot here, not in a lifespan hook: a database that cannot be reached or
    # loaded should stop the process before it answers a single request.
    fees.boot()
    app.state.fees = fees
    # The pipeline prices its comparator cohort from the same schedules the
    # Price tab answers from, so the two cannot disagree.
    app.state.runs = RunService(settings, fees.schedule_for)

    @app.exception_handler(WorthFeesError)
    def fee_error(_: Request, exc: WorthFeesError) -> JSONResponse:
        """The package's refusals, verbatim. A refusal is a result, not a failure."""
        return JSONResponse(
            status_code=422,
            content={"detail": f"{type(exc).__name__}: {exc}", "kind": type(exc).__name__},
        )

    @app.get("/api/health")
    def health(settings: SettingsDep, runs: RunsDep, fees: FeesDep) -> Health:
        pack = load(PACK_NAME)
        return Health(
            api=api_version,
            worth_complexity=complexity_version,
            worth_fees=fees_version,
            rule_pack=RulePackRef(id=pack.rule_pack_id, digest=pack.digest, status=pack.status),
            dataset_dir=str(settings.dataset_dir),
            locality=settings.locality,
            setting=settings.setting,
            database=settings.database_label,
            fees=fees.vintages(),
            latest_run=runs.latest is not None,
        )

    @app.get("/api/dataset")
    def dataset(settings: SettingsDep) -> Dataset:
        return manifest(settings.dataset_dir)

    @app.get("/api/dataset/files/{name}")
    def dataset_file(name: str, settings: SettingsDep) -> DeliveredFile:
        try:
            return file_payload(settings.dataset_dir, name)
        except UnknownFileError as exc:
            raise HTTPException(status_code=404, detail=f"no such file: {name}") from exc

    @app.get("/api/dataset/archive")
    def dataset_archive(settings: SettingsDep) -> Response:
        """The whole dataset as a zip, with a SHA256SUMS list inside."""
        stamp = manifest(settings.dataset_dir).content_hash[:12]
        return Response(
            content=archive(settings.dataset_dir),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="worth-dataset-{stamp}.zip"'},
        )

    @app.get("/api/dataset/raw/{path:path}")
    def dataset_raw(path: str, settings: SettingsDep, download: bool = False) -> FileResponse:
        resolved: Path | None = raw_path(settings.dataset_dir, path)
        if resolved is None:
            raise HTTPException(status_code=404, detail="not found")
        headers = {"X-Sha256": sha256_file(resolved)}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{resolved.name}"'
        return FileResponse(resolved, media_type="text/plain; charset=utf-8", headers=headers)

    @app.get("/api/rulepack")
    def rulepack() -> Scoring:
        return Scoring(rule_pack=rule_pack(load(PACK_NAME)), note_engine=note_engine())

    @app.post("/api/runs")
    def create_run(runs: RunsDep) -> RunResult:
        return runs.execute()

    @app.get("/api/runs/latest")
    def latest_run(runs: RunsDep) -> RunResult:
        latest = runs.latest
        if latest is None:
            raise HTTPException(status_code=404, detail="no run yet; POST /api/runs")
        return latest

    @app.get("/api/fees/vintages")
    def fee_vintages(fees: FeesDep) -> list[VintageInfo]:
        return fees.vintages()

    @app.get("/api/fees/localities")
    def fee_localities(fees: FeesDep, date: str = "today") -> list[Locality]:
        return fees.localities(date)

    @app.post("/api/fees/price")
    def fee_price(request: PriceRequest, fees: FeesDep) -> Derivation:
        return derivation(fees.price(request))

    return app


def serve() -> None:
    """``worth-api``: the development server."""
    uvicorn.run("worth_api.main:create_app", factory=True, host="0.0.0.0", port=8000)


def openapi() -> None:
    """``worth-api-openapi``: the contract, as JSON on stdout.

    The console's TypeScript types are generated from this and committed. CI
    regenerates them and fails on a diff. Built without a database so the
    document is the same on every machine.
    """
    print(json.dumps(create_app(Settings.from_env({})).openapi(), indent=2))
