-- 0001_fee_schedule.sql
--
-- Medicare Physician Fee Schedule reference data.
--
-- Everything in this schema is public CMS data. It contains no PHI, no
-- institution or payer rates, and no CPT descriptors. It can be dropped and
-- rebuilt from the published CMS archives at any time.
--
-- Two rules the schema enforces rather than documents:
--
--   1. Money and RVUs are `numeric`. Never `real` or `double precision`. A
--      binary float cannot represent 0.01, and a fee schedule computed in
--      floats is not reproducible.
--
--   2. A release is immutable. Rows are never updated in place. CMS reissues
--      files (RVU24AR, RVU25D-0, rvu26a-updated-12-29-2025), and each reissue
--      is a new release that supersedes the old one. Mutating rows in place
--      would make the recorded sha256 meaningless and leave you unable to
--      explain an amount you published last year.

BEGIN;

-- ---------------------------------------------------------------------------
-- Releases: the time dimension and the provenance anchor, in one table.
-- ---------------------------------------------------------------------------

CREATE TABLE fee_schedule_release (
    release_id        text        PRIMARY KEY,          -- 'RVU26A'
    rule_year         smallint    NOT NULL,
    quarter           smallint    NOT NULL CHECK (quarter BETWEEN 1 AND 4),

    -- CY2026 has two conversion factors. Which one this release carries is
    -- part of its identity, not a runtime flag.
    payment_basis     text        NOT NULL
                      CHECK (payment_basis IN ('non-qualifying-apm', 'qualifying-apm')),

    -- CMS publishes the work GPCI with and without the 1.0 floor because
    -- whether the floor is in force is a statutory question. Recording the
    -- choice here versions it with the data instead of burying it in code.
    work_gpci_basis   text        NOT NULL CHECK (work_gpci_basis IN ('floor', 'no-floor')),

    conversion_factor numeric(12,4) NOT NULL CHECK (conversion_factor > 0),

    released_on       date        NOT NULL,             -- the date CMS stamped it
    effective         daterange   NOT NULL,             -- service dates these rates govern
    source_url        text        NOT NULL,
    archive_sha256    char(64)    NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    retrieved_at      timestamptz NOT NULL,             -- when we fetched it

    superseded_by     text        REFERENCES fee_schedule_release(release_id),
    CHECK (superseded_by IS DISTINCT FROM release_id)
);

-- Among releases still in force, no two of the same payment basis may cover
-- the same service date. This is what makes "which rate applied on 2026-03-14?"
-- a question with exactly one answer.
--
-- Requires btree_gist (standard contrib; present on RDS, Aurora and most
-- managed Postgres). Installed conditionally so a stripped-down Postgres
-- without contrib still gets a usable schema -- but it warns rather than
-- passing silently, because losing this guard loses the invariant.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    ALTER TABLE fee_schedule_release
        ADD CONSTRAINT fee_schedule_release_no_overlap
        EXCLUDE USING gist (payment_basis WITH =, effective WITH &&)
        WHERE (superseded_by IS NULL);
EXCEPTION
    WHEN undefined_file OR insufficient_privilege OR feature_not_supported THEN
        RAISE WARNING
            'btree_gist unavailable: release-overlap guard NOT installed. '
            'Two releases of the same payment basis could claim the same service date. '
            'Install contrib and re-run: ALTER TABLE fee_schedule_release ADD CONSTRAINT ...';
END $$;

COMMENT ON TABLE  fee_schedule_release IS
    'One row per CMS fee schedule publication. Carries the conversion factor, the '
    'period the rates govern, and the hash of the archive everything was read from.';
COMMENT ON COLUMN fee_schedule_release.effective IS
    'Service-date range these rates govern. Use @> to resolve a claim date to a release.';
COMMENT ON COLUMN fee_schedule_release.retrieved_at IS
    'The second clock: when we learned the rate, as distinct from when it applied.';

-- ---------------------------------------------------------------------------
-- Provenance chain: committed fixture -> CMS member file -> CMS archive.
-- ---------------------------------------------------------------------------

CREATE TABLE source_file (
    release_id   text     NOT NULL REFERENCES fee_schedule_release ON DELETE CASCADE,
    role         text     NOT NULL,     -- 'archive' | 'pprrvu' | 'gpci' | '*_fixture'
    filename     text     NOT NULL,
    sha256       char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    note         text,
    derived_from text,                  -- the role of the upstream file, same release

    PRIMARY KEY (release_id, role),
    FOREIGN KEY (release_id, derived_from)
        REFERENCES source_file (release_id, role) DEFERRABLE INITIALLY DEFERRED,
    CHECK (derived_from IS DISTINCT FROM role)
);

COMMENT ON TABLE source_file IS
    'Every file a derivation depended on, with its sha256. Self-referencing, so a '
    'stripped fixture chains back through the CMS member file to the release archive.';

-- ---------------------------------------------------------------------------
-- RVU components. Grain: one row per (release, code, modifier).
--
-- There is deliberately no descriptor column. CPT descriptors are
-- AMA-copyrighted; you cannot leak what you never stored.
-- ---------------------------------------------------------------------------

CREATE TABLE rvu (
    release_id            text    NOT NULL REFERENCES fee_schedule_release ON DELETE CASCADE,
    hcpcs                 text    NOT NULL CHECK (hcpcs ~ '^[A-Z0-9]{5}$'),
    -- CMS publishes RVU rows under '', '26', 'TC' and '53'. The table records
    -- what CMS published; which of those worth-fees is willing to price is an
    -- application decision, not a storage one.
    modifier              text    NOT NULL DEFAULT '' CHECK (modifier ~ '^[A-Z0-9]{0,2}$'),

    -- Which code system owns this code. CPT is AMA-copyrighted; HCPCS Level II
    -- is CMS-created and public domain. Publication decisions differ, so the
    -- distinction is a column rather than something re-derived downstream.
    code_system           text    NOT NULL
                          CHECK (code_system IN ('cpt-i','cpt-ii','cpt-iii','hcpcs-ii','unknown')),

    status_code           char(1) NOT NULL,
    global_days           text,        -- '000','010','090','XXX','YYY','ZZZ','MMM'

    work_rvu              numeric(9,2) NOT NULL CHECK (work_rvu >= 0),
    pe_rvu_nonfacility    numeric(9,2) NOT NULL CHECK (pe_rvu_nonfacility >= 0),
    pe_rvu_nonfacility_na boolean      NOT NULL,
    pe_rvu_facility       numeric(9,2) NOT NULL CHECK (pe_rvu_facility >= 0),
    pe_rvu_facility_na    boolean      NOT NULL,
    mp_rvu                numeric(9,2) NOT NULL CHECK (mp_rvu >= 0),

    source_role           text    NOT NULL,

    PRIMARY KEY (release_id, hcpcs, modifier),
    FOREIGN KEY (release_id, source_role) REFERENCES source_file (release_id, role)
);

CREATE INDEX rvu_code_system_idx ON rvu (code_system);
CREATE INDEX rvu_status_idx      ON rvu (release_id, status_code);

COMMENT ON COLUMN rvu.pe_rvu_facility_na IS
    'CMS repeats the other setting''s value in an NA column. Reading the number '
    'without checking this flag yields a plausible amount for a service that is '
    'not payable in that setting at all.';
COMMENT ON COLUMN rvu.global_days IS
    'Global surgical period. Load-bearing for cross-specialty comparison: two '
    'procedures with matched operative time but 10- vs 90-day globals are not '
    'comparable on payment alone.';

-- ---------------------------------------------------------------------------
-- Geographic practice cost indices. Grain: one row per (release, locality).
-- ---------------------------------------------------------------------------

CREATE TABLE gpci (
    release_id         text NOT NULL REFERENCES fee_schedule_release ON DELETE CASCADE,
    locality           text NOT NULL CHECK (locality ~ '^[A-Z]{2}[0-9]{2}$'),  -- 'CA18'
    state              char(2) NOT NULL,
    locality_number    char(2) NOT NULL,
    locality_name      text NOT NULL,
    mac                text NOT NULL,

    work_gpci_no_floor numeric(6,3) NOT NULL CHECK (work_gpci_no_floor > 0),
    work_gpci_floor    numeric(6,3) NOT NULL CHECK (work_gpci_floor > 0),
    pe_gpci            numeric(6,3) NOT NULL CHECK (pe_gpci > 0),
    mp_gpci            numeric(6,3) NOT NULL CHECK (mp_gpci > 0),

    source_role        text NOT NULL,

    PRIMARY KEY (release_id, locality),
    CHECK (locality = state || locality_number),
    CHECK (work_gpci_floor >= work_gpci_no_floor),
    FOREIGN KEY (release_id, source_role) REFERENCES source_file (release_id, role)
);

-- ---------------------------------------------------------------------------
-- Rounding.
--
-- Postgres `round(numeric, n)` rounds half AWAY FROM ZERO. worth-fees rounds
-- half to EVEN. On an exact tie they disagree by a cent, which is precisely
-- the kind of silent divergence this project exists to prevent. This function
-- matches the Python.
-- ---------------------------------------------------------------------------

CREATE FUNCTION round_half_even(v numeric, places integer)
RETURNS numeric
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    factor numeric := power(10::numeric, places);
    scaled numeric := v * factor;
    lower  numeric := floor(scaled);
    frac   numeric := scaled - lower;
BEGIN
    IF frac > 0.5 THEN
        lower := lower + 1;
    ELSIF frac = 0.5 AND (lower - 2 * floor(lower / 2)) <> 0 THEN
        lower := lower + 1;   -- exact tie: step to the even neighbour
    END IF;
    -- The half-even decision is already made; round() here only fixes the
    -- result scale and cannot change the value.
    RETURN round(lower / factor, places);
END;
$$;

-- ---------------------------------------------------------------------------
-- The allowed amount. A view, not a table.
--
-- The cross product is ~4.2M rows per release and contains no information the
-- inputs do not. The inputs are ~19k + ~109 rows. Compute, do not store.
-- ---------------------------------------------------------------------------

CREATE VIEW allowed_amount AS
SELECT
    rel.release_id,
    rel.rule_year,
    rel.quarter,
    r.hcpcs,
    r.modifier,
    r.code_system,
    g.locality,
    g.locality_name,
    pos.setting,
    r.work_rvu,
    CASE pos.setting WHEN 'facility' THEN r.pe_rvu_facility ELSE r.pe_rvu_nonfacility END
        AS pe_rvu,
    r.mp_rvu,
    CASE rel.work_gpci_basis WHEN 'floor' THEN g.work_gpci_floor ELSE g.work_gpci_no_floor END
        AS work_gpci,
    g.pe_gpci,
    g.mp_gpci,
    rel.conversion_factor,
    (   r.work_rvu
          * CASE rel.work_gpci_basis WHEN 'floor' THEN g.work_gpci_floor
                                     ELSE g.work_gpci_no_floor END
      + CASE pos.setting WHEN 'facility' THEN r.pe_rvu_facility
                         ELSE r.pe_rvu_nonfacility END * g.pe_gpci
      + r.mp_rvu * g.mp_gpci
    ) AS adjusted_rvu_total,
    round_half_even(
        (   r.work_rvu
              * CASE rel.work_gpci_basis WHEN 'floor' THEN g.work_gpci_floor
                                         ELSE g.work_gpci_no_floor END
          + CASE pos.setting WHEN 'facility' THEN r.pe_rvu_facility
                             ELSE r.pe_rvu_nonfacility END * g.pe_gpci
          + r.mp_rvu * g.mp_gpci
        ) * rel.conversion_factor, 2) AS amount
FROM rvu r
JOIN fee_schedule_release rel USING (release_id)
JOIN gpci g                   USING (release_id)
CROSS JOIN (VALUES ('non-facility'), ('facility')) AS pos(setting)
WHERE r.status_code = 'A'
  -- '' is the global service, 26 the professional and TC the technical
  -- component: all three are priced by the base formula. Modifier 53
  -- (discontinued procedure) has RVU rows in the CMS file but scales the
  -- allowed amount, so pricing it here would return the unadjusted figure --
  -- a wrong answer that looks like a right one. The rows stay in `rvu`;
  -- they are simply not priced. This keeps the view in step with
  -- worth_fees.expected_allowed(), which refuses the same modifier.
  AND r.modifier IN ('', '26', 'TC')
  AND NOT (pos.setting = 'facility'     AND r.pe_rvu_facility_na)
  AND NOT (pos.setting = 'non-facility' AND r.pe_rvu_nonfacility_na);

COMMENT ON VIEW allowed_amount IS
    'Payable Medicare PFS amounts. Non-payable status codes and NA settings are '
    'filtered out, so a plain SELECT cannot return a number for a service that '
    'has no allowed amount.';

COMMIT;
