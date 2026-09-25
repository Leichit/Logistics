"""Collect coarse device facts and create a keyed pseudonymous fingerprint.

Hardware serials, MAC addresses, CPU identifiers and GPS are deliberately not read.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import urllib.error
import urllib.request


def collect() -> dict[str, str]:
    facts = {"system": platform.system(), "release": platform.release(),
             "machine": platform.machine(), "processor_family": platform.processor()[:80]}
    key = os.getenv("AGENT_HMAC_KEY")
    if not key:
        raise RuntimeError("Задайте AGENT_HMAC_KEY: ключ обеспечивает стабильный псевдоним в рамках проекта")
    digest = hmac.new(key.encode(), json.dumps(facts, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return {"fingerprint": digest, "facts": facts}


def submit(api: str, tender_id: str, device_id: str) -> None:
    profile = collect()
    payload = {"device_id": device_id, "device_fingerprint": profile["fingerprint"],
               "observed_specs": profile["facts"], "actor": "school-agent"}
    request = urllib.request.Request(f"{api.rstrip('/')}/tenders/{tender_id}/scans",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(response.read().decode())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"API вернуло {error.code}: {error.read().decode()}") from error


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Локальный агент подтверждения устройства")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--tender", required=True)
    parser.add_argument("--device", required=True, help="ID/QR устройства из зарегистрированной партии")
    args = parser.parse_args()
    submit(args.api, args.tender, args.device)
