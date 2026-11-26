#!/usr/bin/env python3
"""Raise an integration from E1 to E2 with real, billed observations.

One script for every integration, because the shape of an E2 proof does
not change: a real host call, a real submission, and the observation
**re-read from the API** — an acknowledged submission is not a persisted
one.

    python tools/qualify_e2.py litellm --dry-run          # free
    python tools/qualify_e2.py litellm --max-billed 10

🔴 **It spends money.** Each submitted observation is metered (TIER_03:
0.030 CHF). The ceiling is enforced before anything is sent, not after.

🔴 **It refuses to run without prior human authorisation.** `--authorised-by`
must name who approved it and `--budget-chf` must state the ceiling they
approved. Both are written into the evidence, so a proof says who allowed
it to exist.

Credentials: `NEOMUNDI_VERIFICATION_KEY`, read from the environment, never
printed and never written — not even a fragment. Run the service account
guard before this, every time.

The integration supplies the measuring part through a small contract: a
module `qualify.py` next to its `INTEGRATION.yaml`, exposing

    PROBE_NAME: str
    def probe(client, *, submit: bool, metadata: dict) -> list[dict]

Each dict is one attempt: ``{"phase", "submitted", "request_id",
"latency_ms", "token_count", "status", "reason"}``. The integration decides
how to drive its own host; this script owns the budget, the guard, the
re-read and the evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

BASE_URL = "https://api.neomundi.io"
COST_PER_OBSERVATION_CHF = 0.030


def load_probe(slug: str):
    path = ROOT / "integrations" / slug / "qualify.py"
    if not path.is_file():
        raise SystemExit(
            f"REFUSED: {path.relative_to(ROOT)} does not exist.\n"
            "An integration declares how to drive its own host for an E2 proof.\n"
            "See the contract in this script's docstring."
        )
    spec = importlib.util.spec_from_file_location(f"qualify_{slug}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    for attr in ("PROBE_NAME", "probe"):
        if not hasattr(module, attr):
            raise SystemExit(f"REFUSED: {path.name} exposes no {attr}")
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="integration slug, e.g. litellm")
    parser.add_argument(
        "--authorised-by", required=True, help="who approved this run; written into the evidence"
    )
    parser.add_argument(
        "--budget-chf", type=float, required=True, help="the ceiling that was approved, in CHF"
    )
    parser.add_argument(
        "--max-billed", type=int, default=10, help="hard ceiling on submitted observations"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="drive the host and measure locally, submit nothing"
    )
    args = parser.parse_args()

    submit = not args.dry_run
    allowed_by_budget = int(args.budget_chf / COST_PER_OBSERVATION_CHF)
    ceiling = min(args.max_billed, allowed_by_budget)
    if submit and ceiling < 1:
        raise SystemExit(
            f"REFUSED: a budget of {args.budget_chf} CHF allows "
            f"{allowed_by_budget} observations at {COST_PER_OBSERVATION_CHF} CHF each."
        )

    api_key = os.environ.get("NEOMUNDI_VERIFICATION_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "REFUSED: NEOMUNDI_VERIFICATION_KEY is not set.\n"
            "It lives in the Windows USER scope, which a shell does not inherit.\n"
            "Export it explicitly, and run the account guard before measuring."
        )

    module = load_probe(args.slug)
    import neomundi

    stamp = datetime.now(UTC)
    metadata = {
        "role": "devrel-verify",
        "excluded_from": "Study2_Stage1_Stage2_Stage3",
        "integration": f"{args.slug}-neomundi",
        "qualification": "e2",
    }

    print("=" * 72)
    print(f"  E2 qualification — {args.slug}")
    print("=" * 72)
    print(f"  probe            : {module.PROBE_NAME}")
    print(f"  authorised by    : {args.authorised_by}")
    print(f"  budget           : {args.budget_chf} CHF  →  {ceiling} observation(s) max")
    print(f"  submit           : {submit}")
    print(f"  sdk              : neomundi {neomundi.__version__}")
    print("=" * 72)

    client = neomundi.NeoMundiClient(api_key=api_key, base_url=BASE_URL, timeout=60.0)
    records: list[dict[str, Any]] = []
    readback: list[dict[str, Any]] = []

    try:
        print(f"\n[1/2] Driving the host through {module.PROBE_NAME}")
        records = list(module.probe(client, submit=submit, metadata=metadata))

        billed = sum(1 for r in records if r.get("submitted"))
        if billed > ceiling:
            print(
                f"\n::warning:: the probe submitted {billed} observations, above the "
                f"ceiling of {ceiling}. The ceiling is advisory to the probe; "
                "tighten the probe itself."
            )

        for record in records:
            # Every field is formatted through str(): a probe that recorded
            # nothing for a phase must still be printed, not crash the run.
            print(
                f"      {str(record.get('phase')):22} {str(record.get('status')):34} "
                f"tokens={str(record.get('token_count')):>6} "
                f"request_id={record.get('request_id')}"
            )

        print("\n[2/2] Reading the observations back from the API")
        for record in records:
            request_id = record.get("request_id")
            if not record.get("submitted") or not request_id:
                continue
            try:
                response = client._request(  # noqa: SLF001
                    "GET", f"/v1/govern/{request_id}", None, tolerated_statuses=(404,)
                )
                ok = response.status_code == 200
                body = response.json() if ok else {}
                audit = body.get("audit") or {}
                readback.append(
                    {
                        "request_id": request_id,
                        "http_status": response.status_code,
                        "persisted": ok,
                        "measurement_version": audit.get("measurement_version"),
                    }
                )
                print(
                    f"      {request_id}  HTTP {response.status_code}  "
                    f"{'persisted' if ok else 'NOT READABLE'}  "
                    f"mv={audit.get('measurement_version')}"
                )
            except Exception as exc:  # noqa: BLE001
                readback.append(
                    {"request_id": request_id, "persisted": False, "error_type": type(exc).__name__}
                )
                print(f"      {request_id}  readback failed: {type(exc).__name__}")
            time.sleep(0.25)  # stay well under 60 req/min
    finally:
        client.close()

    billed = sum(1 for r in records if r.get("submitted"))
    persisted = sum(1 for r in readback if r.get("persisted"))
    versions = sorted(
        {r.get("measurement_version") for r in readback if r.get("measurement_version")}
    )

    evidence = {
        "proof": f"{args.slug}-neomundi E2 qualification",
        "started_utc": stamp.isoformat(),
        "finished_utc": datetime.now(UTC).isoformat(),
        "api_base_url": BASE_URL,
        "probe": module.PROBE_NAME,
        "authorisation": {
            "authorised_by": args.authorised_by,
            "budget_chf": args.budget_chf,
            "ceiling_observations": ceiling,
        },
        "account": {
            # No key material, not even a fragment: the account identity is
            # established by the guard, not by bytes of the key.
            "note": "service verification account; guard authorised before this run",
        },
        "versions": {"neomundi": neomundi.__version__, "python": sys.version.split()[0]},
        "metadata_applied": metadata,
        "billed_observations": billed,
        "estimated_cost_chf": round(billed * COST_PER_OBSERVATION_CHF, 3),
        "measurement_versions_served": versions,
        "records": records,
        "readback": readback,
        "note": (
            "Prompts and responses are deliberately absent. Observations are "
            "marked role=devrel-verify and must be excluded from any analysis."
        ),
    }

    out_dir = ROOT / "integrations" / args.slug / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"e2_qualification_{stamp:%Y%m%dT%H%M%SZ}.json"
    out_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"  billed              : {billed}  (~{billed * COST_PER_OBSERVATION_CHF:.2f} CHF)")
    print(f"  re-read as persisted: {persisted}/{len(readback)}")
    print(f"  measurement version : {', '.join(versions) or '—'}")
    print(f"  evidence            : {out_path.relative_to(ROOT)}")
    print("=" * 72)

    if submit and persisted == 0:
        print("\nE2 NOT established: nothing was re-read as persisted.", file=sys.stderr)
        return 1
    if submit:
        print("\nE2 established. Update INTEGRATION.yaml, then let the conformance")
        print("check verify the numbers rather than trusting them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
