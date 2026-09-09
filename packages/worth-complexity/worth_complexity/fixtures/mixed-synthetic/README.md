# Mixed-synthetic dataset

**Fabricated data. No patient, encounter, clinician, payer contract or dollar
amount here corresponds to anything real.** It exists so `pipeline.run` can
be exercised on one extract holding OR cases, clinic visits and monitoring
episodes side by side, sharing one 835/837 feed — the shape a real partner
delivery actually takes (CONTRACT-PACKS-MC.md, "a real partner delivery is
one extract with OR cases, clinic visits and monitoring episodes side by
side").

Built by `tools/make_mixed_dataset.py`, which runs the surgical
(`make_synthetic_dataset.py`), visit (`make_visit_dataset.py`) and episode
(`make_episode_dataset.py`) generators each into their own scratch directory
and merges the three `clinical/`, `remittance/` and `claims/` directories
into this one. It does not regenerate, and never writes into, any of the
three single-class fixtures those generators otherwise produce
(`mssm-synthetic/`, `visit-synthetic/`, `episode-synthetic/`).

Regenerate it with `just build-dataset mixed`. It is deterministic: every
class's own generator is (each individually documented in its own module
docstring), so the merge is too.

## What is here

Every table `cases.py`/`visits.py`/`episodes.py` reads, for all three
classes, in one `clinical/` directory: `or_log.txt` (surgical's anchor),
`visit.txt` (visit's), `episode.txt` (episode's), and every table each class
needs beside its own anchor. Three tables are shared rather than duplicated:
`patient_lds.txt` and `problem_list.txt` are a straight union of rows (every
class that carries them uses the same columns); `encounter_dx.txt` is a
union of columns as well as rows, since surgical's and visit's own column
names for the same idea (`seq`/`poa_yn` vs `sequence`/`addressed`) differ.
`notes/` merges surgical's and visit's own note files (episode carries none).

`remittance/` and `claims/` hold every class's own 835/837 files, prefixed
by class (`surgical_835_...edi`, `visit_835_...edi`, `episode_835_...edi`)
so the three can never collide on disk, with each class's envelope control
numbers (ISA/GS/ST/SE/GE/IEA) offset so they do not collide inside the
files either. See `tools/make_mixed_dataset.py`'s own module docstring for
exactly which id spaces were checked non-colliding and why (billing account
ids, patient ids, encounter/visit/episode ids, CSNs) and which numbers were
deliberately left alone (835 TRN02, CLP07 — trace numbers neither X12 nor
this package's readers require to be globally unique).

## Running it

```
uv run worth-cli run --clinical mixed-synthetic/clinical \
    --remittance mixed-synthetic/remittance --locality NY01
uv run worth-cli compare --domains --clinical mixed-synthetic/clinical \
    --remittance mixed-synthetic/remittance --locality NY01
```

`pipeline.run` resolves `extracts.CombinedExtract` for this directory (three
anchors present) and produces a `Run` with three `ClassRun`s, one per class,
each with its own packaged pack (`surgical-v1`, `visit-em-v1`,
`episode-rpm-v1`) and its own place-of-service setting (facility for
surgical, non-facility for visit and episode) — nothing passed, the class
defaults resolving exactly as they do for each single-class fixture.
