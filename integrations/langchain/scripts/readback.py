#!/usr/bin/env python3
"""Read previously submitted observations back from the API.

A read, not a measurement: it confirms persistence without creating a new
observation or debiting the wallet again.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import neomundi  # noqa: E402

evidence_path = Path(sys.argv[1])
evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
ids = [r["request_id"] for r in evidence["records"] if r.get("submitted") and r.get("request_id")]
key = os.environ["NEOMUNDI_VERIFICATION_KEY"].strip()
client = neomundi.NeoMundiClient(api_key=key, base_url=evidence["api_base_url"], timeout=30.0)
out = []
try:
    for rid in ids:
        r = client._request("GET", f"/v1/govern/{rid}", None, tolerated_statuses=(404,))
        ok = r.status_code == 200
        body = r.json() if ok else {}
        audit = body.get("audit") or {}
        out.append(
            {
                "request_id": rid,
                "http_status": r.status_code,
                "persisted": ok,
                "measurement_version": audit.get("measurement_version"),
                "mode": body.get("mode"),
                "has_governance": "governance" in body,
                "unknown_top_level": sorted(
                    set(body)
                    - {
                        "request_id",
                        "mode",
                        "governance",
                        "metrics",
                        "baseline",
                        "audit",
                        "contract_version",
                        "token_count_source",
                        "token_count_scope",
                        "token_count_unit",
                        "measurement",
                        "quality",
                        "runtime",
                        "esi",
                        "density",
                        "rag_grounding",
                        "evaluators",
                        "timestamp",
                        "model",
                        "provider",
                        "system_id",
                        "g_score",
                        "g_final",
                        "v_score",
                        "delta_g",
                        "latency_ms",
                        "processing_time_ms",
                        "measured_signals",
                        "unmeasured_signals",
                        "checks_emitted",
                        "trajectory_points",
                    }
                ),
            }
        )
        print(
            f"  {rid}  HTTP {r.status_code}  {'persisted' if ok else 'NOT FOUND'}  mv={audit.get('measurement_version')}"
        )
        time.sleep(0.25)
finally:
    client.close()
evidence["readback"] = out
evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n  {sum(1 for o in out if o['persisted'])}/{len(out)} relues comme persistees")
