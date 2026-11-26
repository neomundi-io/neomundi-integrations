# Maintenance policy

[Index](README.md) · [Catalogue](CATALOG.md) · [Contributing](CONTRIBUTING.md)

A library of integrations does not fail by being hard to build. It fails by
rotting quietly while its index keeps claiming everything is fine. This
document says what is actively watched, how often, and what happens when
something breaks.

---

## What is watched, automatically

| Check | When | What it catches |
| --- | --- | --- |
| Full suite, every integration, at its declared bounds | every push and pull request | a regression we introduced |
| Conformance and evidence verification | every push and pull request | a claim that stopped being true |
| Generated index freshness | every push and pull request | an index drifting from reality |
| **Every suite against the LATEST upstream release** | **weekly, Monday 06:17 UTC** | an upstream release breaking users who install today |

The weekly run is the important one. CI tests the *verified bounds*
declared in each `INTEGRATION.yaml`; a developer installing today gets
whatever the host technology released this morning. Without that run, an
upstream break is discovered by a user rather than by us.

A failure there opens an issue labelled `upstream-drift` and
`integration:<slug>`, with a link to the failing run. It is deduplicated,
so a persistent break does not produce an issue every week.

## What a maintainer owes each integration

- **Keep `last_verified` honest.** It is a date in `INTEGRATION.yaml`, and
  it means *somebody verified this then*, not *this probably still works*.
- **Widen the verified bounds deliberately.** When a new upstream release
  passes, bump `compatibility.target.verified` in the same pull request
  that proves it.
- **State an incompatibility rather than hiding it.** If the latest
  upstream genuinely breaks the integration and the fix is not immediate,
  write it in `LIMITATIONS.md` and leave the drift issue open. A documented
  limit is useful; a silent one is a trap.
- **Re-run the execution proof when the measurement version moves.** Two
  results produced under two different `measurement_version` values are not
  comparable for the same input, so an E2 claim ages.

## Support window

An integration is supported while its `maturity` is `beta`, `stable` or
`maintained`. `experimental` carries no commitment beyond its tests being
green at the stated bounds.

There is no date-based end of life. An integration stops being supported
when it is explicitly marked `deprecated`, which requires a
`DEPRECATION.md` saying why, since when, and what to use instead.

## Deprecation

1. `maturity: deprecated` in `INTEGRATION.yaml`.
2. A `DEPRECATION.md` with the reason, the date and the migration path.
3. The index renders deprecated entries separately, so a reader cannot
   miss the status.

The package stays installable. Removing a published version from PyPI
breaks every build that pinned it, which punishes the users who trusted us
earliest.

## Archiving

Move the directory to `integrations/_archived/<slug>/`.

🔴 **Nothing is ever deleted.** Evidence has to outlive the code it
attests: an archived integration keeps its proofs, its limitations and its
mission report, and stays listed under *Archived* in the index. A catalogue
that erases its own history cannot be audited.
