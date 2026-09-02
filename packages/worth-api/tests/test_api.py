"""The routes, end to end through the test client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from worth_api.main import create_app
from worth_api.settings import Settings
from worth_complexity.pipeline import STAGES

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_health_names_what_this_instance_is_pointed_at(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["worthComplexity"] == "0.1.0"
    assert body["rulePack"]["status"] == "provisional"
    assert body["datasetDir"].endswith("mssm-synthetic")
    assert body["database"] == "not configured"
    assert body["latestRun"] is False


def test_the_manifest_lists_every_delivered_file_closed(client: TestClient) -> None:
    body = client.get("/api/dataset").json()
    names = [f["name"] for f in body["files"]]
    assert names == [
        "or_log.txt",
        "or_log_proc.txt",
        "or_staff.txt",
        "encounter_dx.txt",
        "patient_lds.txt",
        "notes/",
        "remittance/",
    ]
    table = body["files"][0]
    assert table["kind"] == "table" and table["rows"] > 0 and "rows" in table
    assert "rows" not in body["files"][5]  # a bundle carries a count, not contents
    assert body["study"] + body["comparator"] == body["encounters"]
    assert body["codes"] == ["58558", "58563", "58570", "58661", "58662"]
    assert body["periodStart"] < body["periodEnd"]
    assert len(body["contentHash"]) == 64


def test_a_table_is_served_whole_on_request(client: TestClient) -> None:
    body = client.get("/api/dataset/files/or_log.txt").json()
    assert body["kind"] == "table"
    assert body["columns"][0] == "log_id"
    assert all(len(row) == len(body["columns"]) for row in body["rows"])


def test_the_bundles_answer_to_their_short_names(client: TestClient) -> None:
    notes = client.get("/api/dataset/files/notes").json()
    assert notes["kind"] == "notes" and notes["count"] == len(notes["files"])
    edi = client.get("/api/dataset/files/remittance").json()
    assert edi["kind"] == "edi" and edi["files"][0]["segments"][0].startswith("ISA*")


def test_an_unknown_file_is_not_found(client: TestClient) -> None:
    assert client.get("/api/dataset/files/passwords.txt").status_code == 404


def test_raw_bytes_arrive_with_their_hash(client: TestClient) -> None:
    manifest = client.get("/api/dataset").json()
    expected = manifest["files"][0]["sha256"]
    response = client.get("/api/dataset/raw/clinical/or_log.txt")
    assert response.status_code == 200
    assert response.headers["x-sha256"] == expected
    assert response.text.startswith("log_id|")


def test_raw_refuses_to_leave_the_dataset_root(client: TestClient) -> None:
    """A path that resolves outside the root is not found, not forbidden."""
    for path in ("../../pyproject.toml", "clinical/../../README.md", "/etc/hosts", "clinical"):
        assert client.get(f"/api/dataset/raw/{path}").status_code == 404, path


def test_the_dataset_downloads_as_one_archive_with_checksums(client: TestClient) -> None:
    """A clinician can take the inputs away, read them, change them and hand them back."""
    import hashlib
    import io
    import zipfile

    response = client.get("/api/dataset/archive")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="worth-dataset-'
    )
    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    names = bundle.namelist()
    assert "README.md" in names and "SHA256SUMS" in names
    assert "clinical/or_log.txt" in names and "clinical/notes.txt" in names
    assert sum(n.startswith("clinical/notes/") for n in names) > 100
    assert sum(n.startswith("remittance/") for n in names) > 10
    sums: dict[str, str] = {}
    for line in bundle.read("SHA256SUMS").decode().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    assert (
        sums["clinical/or_log.txt"]
        == hashlib.sha256(bundle.read("clinical/or_log.txt")).hexdigest()
    )
    manifest = client.get("/api/dataset").json()
    assert sums["clinical/or_log.txt"] == manifest["files"][0]["sha256"]


def test_a_raw_file_can_be_asked_for_as_a_download(client: TestClient) -> None:
    inline = client.get("/api/dataset/raw/clinical/or_log.txt")
    assert "content-disposition" not in inline.headers
    saved = client.get("/api/dataset/raw/clinical/or_log.txt", params={"download": "1"})
    assert saved.headers["content-disposition"] == 'attachment; filename="or_log.txt"'
    assert saved.text == inline.text


def test_the_rule_pack_is_served_without_a_run(client: TestClient) -> None:
    body = client.get("/api/rulepack").json()
    assert body["rulePack"]["status"] == "provisional"
    assert len(body["rulePack"]["markers"]) == 11
    assert body["noteEngine"]["lookback"] > 0


def test_there_is_no_latest_run_before_the_first_post() -> None:
    from fastapi.testclient import TestClient as Client
    from worth_complexity.cli import FIXTURE

    fresh = Client(create_app(Settings(dataset_dir=FIXTURE)))
    assert fresh.get("/api/runs/latest").status_code == 404
    assert fresh.get("/api/health").json()["latestRun"] is False


def test_a_run_is_computed_on_request_and_then_remembered(client: TestClient) -> None:
    manifest = client.get("/api/dataset").json()
    first = client.post("/api/runs").json()
    assert first["summary"]["study"] == manifest["study"]
    assert len(first["encounters"]) == manifest["encounters"]
    assert set(first["timings"]) == set(STAGES)
    assert sum(first["timings"].values()) > 0
    assert first["steps"][0]["stage"] == "extract"

    latest = client.get("/api/runs/latest").json()
    assert latest == first
    assert client.get("/api/health").json()["latestRun"] is True

    again = client.post("/api/runs").json()
    assert again == first  # same inputs, same answer, served from the cache


def test_the_openapi_document_carries_the_console_types(client: TestClient) -> None:
    """The TypeScript types are generated from this; the names have to be there."""
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]
    for name in ("RunResult", "Encounter", "IndexRecord", "TableFile", "NoteBundle", "EdiBundle"):
        assert name in schemas, name
    assert "adequacyTrace" in schemas["Encounter"]["properties"]


def test_the_database_url_can_be_assembled_from_libpq_variables() -> None:
    """App Runner injects the RDS secret's password into PGPASSWORD; the rest are plain."""
    settings = Settings.from_env(
        {
            "PGHOST": "db.internal",
            "PGUSER": "worth",
            "PGPASSWORD": "p@ss/word",
            "PGDATABASE": "worth",
            "PGPORT": "5432",
        }
    )
    assert settings.database_url == "postgresql://worth:p%40ss%2Fword@db.internal:5432/worth"
    assert settings.database_label == "db.internal:5432/worth"
    explicit = Settings.from_env({"DATABASE_URL": "postgresql://a:b@h/x", "PGHOST": "ignored"})
    assert explicit.database_url == "postgresql://a:b@h/x"
    assert Settings.from_env({}).database_url is None
