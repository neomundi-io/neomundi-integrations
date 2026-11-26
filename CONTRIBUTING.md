# Contributing an integration

[Français](CONTRIBUTING_FR.md) · [Index](README.md) · [Catalogue](CATALOG.md)

An integration is accepted when it is **useful, honest and provable**. This
document says exactly what those three words mean here, so nobody has to
guess and nobody has to argue.

---

## The shape of an integration

Every integration lives in `integrations/<slug>/` and carries the same
files. Uniformity is not bureaucracy: it is the only thing that keeps a
library of a hundred entries navigable, and it is **enforced by
`tools/check_conformance.py`** rather than hoped for.

```
integrations/<slug>/
├── INTEGRATION.yaml     the single source of truth for this integration
├── README.md            the developer's entry point
├── README_FR.md         the same, in French
├── LIMITATIONS.md       what it does not do, measured rather than assumed
├── VALUE.md             complementarity, business value, ROI arithmetic
├── CHANGELOG.md         per-package, SemVer
├── pyproject.toml       its own dependencies and its own Python floor
├── src/<import_name>/   the code, with py.typed
├── tests/               offline, no key, no cost
├── examples/            runnable, and honest about what they demonstrate
├── notebooks/           at least one reproducible notebook
└── evidence/            required from E2 upward
```

Start from `templates/integration/`. Do not copy another integration: you
will inherit its wording and its assumptions.

## The two axes, and why they are separate

**Maturity** is the package lifecycle: `experimental` → `beta` → `stable`
→ `maintained` → `deprecated` → `archived`. It says how much you should
trust the code.

**Evidence** is what has actually been demonstrated: **E0** nothing yet,
**E1** offline tests green across platforms, **E2** plus real execution
against production with the proof committed, **E3** plus independently
reproduced by a third party.

They are separate because a mature package can be badly evidenced and a
well-evidenced one can still be alpha. The scale is the one already used by
[`neomundi-use-cases`](https://github.com/neomundi-io/neomundi-use-cases/blob/main/CATALOG.md),
**reused rather than redefined** — two competing maturity scales in one
organisation end up with both of them wrong.

### Evidence is computed, not claimed

This is the part that makes the catalogue worth reading.

If your `INTEGRATION.yaml` says `evidence.level: e2`, then
`check_conformance.py` opens the file named by `evidence.proof` and checks
that it reports at least one **submitted** observation, that
`real_observations` matches the number of submitted records, and that
`observations_verified_persisted` matches the number re-read as persisted.
A mismatch fails CI.

So the badge on the index is a **fact**. You cannot promote your own
integration by editing a field.

## Acceptance criteria

Write them down **before** you implement. On the first mission this is what
made a real defect undeniable instead of debatable.

These eight are required of every integration. Add your own on top.

1. A measured interaction produces a persisted observation, and its
   `request_id` is re-read from the API.
2. An interaction that cannot be measured **submits nothing and invents
   nothing**, and says why.
3. **No exception from the integration ever reaches the host
   application** — not from your code, not from the SDK, not from the
   network. Test it under at least four distinct API failures.
4. Reported latency is the host application's call, not the measurement's.
5. Sampling at zero opens no socket and bills nothing.
6. The whole suite runs **offline, with no key and no cost**.
7. The notebook runs end to end on a fresh machine from the README alone.
8. No key, prompt or response appears in any log, outcome or error message.

Criterion 3 is the one that decides whether an integration is safe to
recommend. Instrumentation that can take down the application it observes
has negative value.

## The three refusals, inherited

These are not local style. They come from the `neomundi` SDK and an
integration may not soften them.

- **Nothing is invented.** If the host cannot report a token count, no
  observation is submitted. An estimate would be a fabricated measurement
  attributed to someone's application.
- **Nothing escapes.** No exception reaches the host.
- **Nothing is silent.** Every run yields an explicit outcome.

## Honesty rules the CI enforces

- A package with `published_on: none` may not offer a **copyable**
  `pip install <name>` that would fail. Prose explaining that it does not
  work yet is encouraged; a code block a reader would copy is refused.
- `LIMITATIONS.md` may not be a placeholder. A short honest file passes; an
  empty one does not.
- A live key prefix anywhere in the tree fails the build.

## Cost

🔴 **Every submitted observation is a billed measurement.** There is no
trace-only route on the NeoMundi API.

Consequences for your design, not optional:

- Expose a sampling control, and make it the first thing the cost section
  of your README discusses.
- Offer a mode that measures locally and opens no socket, so a developer
  can wire your integration up for free before spending anything.
- Make the sampling decision **deterministic in the run identifier**, so a
  sampled trace is reproducible rather than a coin flip nobody can replay.

For the execution proof, use the service verification account, run its
guard **before** measuring, and mark every record as technical so it is
excluded from any analysis. Never point a proof script at a pilot account.

## Versioning and release

- SemVer per package, independent of every other integration.
- Tags are `<slug>-v<version>`, e.g. `langchain-v0.1.0`.
- Publication is a **separate, approved act**: a manual dispatch from
  `main`, an existing matching tag, a typed confirmation and an approval on
  the `pypi` environment. It is never a side effect of a merge.

## Opening the pull request

One integration, one pull request. CI will tell you, in this order, if the
skeleton is incomplete, if an evidence claim is unverifiable, if the
generated index is stale, or if a test fails. Run it locally first:

```bash
python -m pip install -r tools/requirements.txt
python tools/check_conformance.py
python tools/render_index.py          # regenerate the index and badges
cd integrations/<slug> && pytest -q
```

Commit the regenerated `README.md`, `README_FR.md`, `CATALOG.md`,
`CATALOG_FR.md`, `registry.json` and `badges/` along with your work. CI
fails if they are stale, because an index that drifts from reality is worse
than no index.

## Deprecating and archiving

Deprecate explicitly: `maturity: deprecated` plus a `DEPRECATION.md` saying
why, since when, and what to use instead.

Archive by moving to `integrations/_archived/<slug>/`. **Nothing is ever
deleted** — evidence has to outlive the code it attests.
