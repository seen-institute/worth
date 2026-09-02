"""Where the server finds things. Environment in, one frozen object out.

Everything a deployment can vary lives here and nowhere else, so the answer to
"what is this instance pointed at" is always ``GET /api/health`` reading the
same object every route reads.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from worth_complexity.cli import FIXTURE

DATASET_DIR_ENV = "WORTH_DATASET_DIR"
DATABASE_URL_ENV = "DATABASE_URL"
LOCALITY_ENV = "WORTH_LOCALITY"
SETTING_ENV = "WORTH_SETTING"

DEFAULT_LOCALITY = "NY01"
"""Manhattan: the synthetic institution's locality. A partner deployment sets its own."""


@dataclass(frozen=True, slots=True)
class Settings:
    dataset_dir: Path
    """A partner dataset: ``clinical/`` and ``remittance/`` side by side.

    Defaults to the synthetic fixture packaged with worth-complexity, which is
    what the public console runs on. Pointing it anywhere else is the whole
    difference between the demonstration and a partner deployment.
    """

    database_url: str | None = None
    """Postgres holding the fee schedule, as a ``postgresql://`` URL.

    ``None`` means no database: prices come from the committed eight-code
    fixture and ``/api/health`` says so. With a URL, the pinned vintages are
    loaded into it at boot and every price is computed from the rows read back.
    """

    locality: str = DEFAULT_LOCALITY
    """The institution's Medicare locality, which the extract does not carry.
    Every comparator encounter is priced in it."""

    setting: str = "facility"
    """``facility`` or ``non-facility``. OR cases are facility."""

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        raw = env.get(DATASET_DIR_ENV)
        dataset_dir = Path(raw).expanduser().resolve() if raw else FIXTURE
        return cls(
            dataset_dir=dataset_dir,
            database_url=env.get(DATABASE_URL_ENV) or database_url_from_pg(env),
            locality=env.get(LOCALITY_ENV) or DEFAULT_LOCALITY,
            setting=env.get(SETTING_ENV) or "facility",
        )

    @property
    def clinical(self) -> Path:
        return self.dataset_dir / "clinical"

    @property
    def remittance(self) -> Path:
        return self.dataset_dir / "remittance"

    @property
    def database_label(self) -> str:
        """The database without its credentials, for health and logs."""
        if self.database_url is None:
            return "not configured"
        parts = urlsplit(self.database_url)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}{parts.path}"


def database_url_from_pg(env: Mapping[str, str]) -> str | None:
    """Assemble a URL from libpq's ``PGHOST``, ``PGUSER``, ``PGPASSWORD`` and friends.

    App Runner injects a Secrets Manager value into one environment variable,
    and the RDS-generated secret is a JSON document rather than a URL; the
    stack maps its ``password`` key to ``PGPASSWORD`` and sets the rest as
    plain variables. Locally, ``DATABASE_URL`` is simpler and wins when set.
    """
    host = env.get("PGHOST")
    if not host:
        return None
    user = quote(env.get("PGUSER", "worth"), safe="")
    password = quote(env.get("PGPASSWORD", ""), safe="")
    database = env.get("PGDATABASE", "worth")
    port = env.get("PGPORT", "5432")
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{database}"
