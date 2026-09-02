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

    -- CMS publishes the work GPCI with and without the 1.0 floor because
    -- whether the floor is in force is a statutory question. Recording the
    -- choice here versions it with the data instead of burying it in code.
    work_gpci_basis   text        NOT NULL CHECK (work_gpci_basis IN ('floor', 'no-floor')),

    released_on       date        NOT NULL,             -- the date CMS stamped it
    effective         daterange   NOT NULL,             -- service dates these rates govern
    source_url        text        NOT NULL,
    archive_sha256    char(64)    NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    retrieved_at      timestamptz NOT NULL,             -- when we fetched it

    superseded_by     text        REFERENCES fee_schedule_release(release_id),
    CHECK (superseded_by IS DISTINCT FROM release_id)
);

-- Among releases still in force, no two may cover the same service date. This
-- is what makes "which rate applied on 2026-03-14?" a question with exactly
-- one answer.
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
        EXCLUDE USING gist (effective WITH &&)
        WHERE (superseded_by IS NULL);
EXCEPTION
    WHEN undefined_file OR insufficient_privilege OR feature_not_supported THEN
        RAISE WARNING
            'btree_gist unavailable: release-overlap guard NOT installed. '
            'Two in-force releases could claim the same service date. '
            'Install contrib and re-run: ALTER TABLE fee_schedule_release ADD CONSTRAINT ...';
END $$;

COMMENT ON TABLE  fee_schedule_release IS
    'One row per CMS fee schedule publication. Carries the period the rates govern '
    'and the hash of the archive everything was read from. Conversion factors hang '
    'off it in fee_schedule_conversion_factor, one per payment basis.';
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
-- Conversion factors. Grain: one row per (release, payment basis).
--
-- From CY2026 CMS publishes the fee schedule twice in one archive: qualifying
-- APM participants are paid on a higher conversion factor than everyone else.
-- The two files carry identical RVUs and payment-policy indicators for every
-- code they share, so the basis changes what a unit is worth and nothing else.
--
-- That is why this is its own table rather than a column on the release, and
-- why `rvu` is not duplicated per basis. Storing a second copy of ~11,800
-- byte-identical rows to vary one number would be the same mistake as
-- materialising `allowed_amount`. worth_fees.sources._cross_check_qpp asserts
-- the two files really do agree on every parse, so this normalisation fails
-- loudly rather than silently if CMS ever diverges them.
-- ---------------------------------------------------------------------------

CREATE TABLE fee_schedule_conversion_factor (
    release_id        text          NOT NULL REFERENCES fee_schedule_release ON DELETE CASCADE,
    payment_basis     text          NOT NULL
                      CHECK (payment_basis IN ('non-qualifying-apm', 'qualifying-apm')),
    conversion_factor numeric(12,4) NOT NULL CHECK (conversion_factor > 0),
    source_role       text          NOT NULL,

    PRIMARY KEY (release_id, payment_basis),
    FOREIGN KEY (release_id, source_role) REFERENCES source_file (release_id, role)
);

COMMENT ON TABLE fee_schedule_conversion_factor IS
    'Dollars per RVU, one row per payment basis. Join to allowed_amount to price a '
    'code on a given basis; the RVUs themselves do not vary between bases.';

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

    -- Whether the qualifying-APM file also carries this code. False only for
    -- codes that are non-payable under every basis, which is asserted on
    -- ingest rather than assumed, so this never decides whether a price
    -- exists. It records which of CMS's two files a row was seen in.
    qpp_eligible          boolean NOT NULL DEFAULT true,

    -- Payment-policy indicators. Stored, never priced: worth-fees rejects the
    -- modifiers that would trigger these adjustments rather than modelling
    -- them. Kept verbatim as text, since an unexpected value is information
    -- and a normalised one is a guess.
    pctc_indicator        text,
    multiple_procedure    text,
    bilateral_surgery     text,
    assistant_surgery     text,
    co_surgery            text,
    team_surgery          text,
    endoscopic_base       text CHECK (endoscopic_base IS NULL OR endoscopic_base ~ '^[A-Z0-9]{5}$'),

    -- Shares of the work RVU by phase of care. They sum to 1.00 for a code
    -- with a global period and are all zero otherwise, so the constraint
    -- permits exactly those two shapes.
    pre_op_share          numeric(5,2) NOT NULL DEFAULT 0 CHECK (pre_op_share   BETWEEN 0 AND 1),
    intra_op_share        numeric(5,2) NOT NULL DEFAULT 0 CHECK (intra_op_share BETWEEN 0 AND 1),
    post_op_share         numeric(5,2) NOT NULL DEFAULT 0 CHECK (post_op_share  BETWEEN 0 AND 1),
    CHECK (pre_op_share + intra_op_share + post_op_share IN (0, 1)),

    source_role           text    NOT NULL,

    PRIMARY KEY (release_id, hcpcs, modifier),
    FOREIGN KEY (release_id, source_role) REFERENCES source_file (release_id, role)
);

CREATE INDEX rvu_code_system_idx ON rvu (code_system);
CREATE INDEX rvu_status_idx      ON rvu (release_id, status_code);

COMMENT ON COLUMN rvu.post_op_share IS
    'Share of the work RVU attributed to post-operative care. Non-zero only for '
    'codes with a global period, and never above 0.23 in CY2026. Useful for '
    'cross-specialty comparison: two codes at equal work RVUs and equal global '
    'periods still differ in what the payment is buying.';

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
-- Locality-to-county crosswalk. Grain: one row per (release, MAC, locality).
--
-- Reference data, never priced from. It answers "what geography does CA18
-- cover?" and it deliberately does NOT answer "what locality is this address
-- in?": the county text is CMS's own free-form prose, typos included, and
-- normalising it would mean storing guesses about what CMS meant. For address
-- resolution CMS publishes a separate ZIP-code file, which worth-fees does not
-- pin today.
--
-- Note this joins to `gpci` on locality_number and MAC, not on state: this
-- file names states in full ('CALIFORNIA') while the GPCI file uses the
-- two-letter code, and inventing a mapping between them is exactly the kind
-- of unverifiable guess the rest of this schema avoids.
-- ---------------------------------------------------------------------------

CREATE TABLE locality_county (
    release_id        text NOT NULL REFERENCES fee_schedule_release ON DELETE CASCADE,
    -- Position in the CMS file, from 1. The key, because a locality can span
    -- several rows and CY2026 repeats one outright: Missouri's rest-of-state
    -- is serviced by two carriers and appears twice, identical but for
    -- trailing whitespace. Keying on the row stores the file as published.
    ordinal           int  NOT NULL CHECK (ordinal > 0),
    mac               text NOT NULL,
    state_name        text NOT NULL,     -- 'CALIFORNIA', not 'CA'
    locality_number   text NOT NULL,
    fee_schedule_area text,
    counties          text,              -- free text; 'ALL COUNTIES' if statewide
    source_role       text NOT NULL,

    PRIMARY KEY (release_id, ordinal),
    FOREIGN KEY (release_id, source_role) REFERENCES source_file (release_id, role)
);

CREATE INDEX locality_county_locality_idx ON locality_county (release_id, mac, locality_number);

COMMENT ON TABLE locality_county IS
    'Which counties each Medicare locality covers, in CMS''s own words. Reference '
    'data: nothing is priced from it, and it cannot resolve an address to a locality.';
COMMENT ON COLUMN locality_county.counties IS
    'Verbatim CMS text, typos preserved (CY2026 spells Orange county ''ORAGNGE''). '
    'Cleaning it up would be an unverifiable judgement call about CMS''s intent.';

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
--
-- Now crossed with payment basis as well, so a row is one (code, locality,
-- setting, basis). The qualifying-APM basis is restricted to codes CMS carries
-- in its QPP file, which across CY2026 is every payable code.
-- ---------------------------------------------------------------------------

CREATE VIEW allowed_amount AS
SELECT
    rel.release_id,
    rel.rule_year,
    rel.quarter,
    cf.payment_basis,
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
    cf.conversion_factor,
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
        ) * cf.conversion_factor, 2) AS amount
FROM rvu r
JOIN fee_schedule_release rel USING (release_id)
JOIN fee_schedule_conversion_factor cf USING (release_id)
JOIN gpci g                   USING (release_id)
CROSS JOIN (VALUES ('non-facility'), ('facility')) AS pos(setting)
WHERE r.status_code = 'A'
  -- The qualifying-APM conversion factor only applies to codes CMS actually
  -- publishes in that file. Without this, a code absent from the QPP file
  -- would still get a qualifying-basis price computed from the other file.
  AND (cf.payment_basis <> 'qualifying-apm' OR r.qpp_eligible)
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
    'Payable Medicare PFS amounts, one row per code, locality, setting and payment '
    'basis. Non-payable status codes, NA settings and codes absent from the chosen '
    'basis are filtered out, so a plain SELECT cannot return a number for a service '
    'that has no allowed amount. Filter on payment_basis unless you want both.';

COMMIT;
