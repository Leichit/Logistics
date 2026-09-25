"""Explainable baseline risk signals; not a trained or decision-making AI model."""
from __future__ import annotations

from typing import Any


def scan_risk(payload: dict[str, Any], previous_scans: list[dict[str, Any]]) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0
    elapsed = payload.get("scan_duration_seconds")
    if elapsed is not None and elapsed < 2:
        score += 35
        reasons.append("Сканирование заняло менее 2 секунд")
    if payload.get("virtualized") is True:
        score += 45
        reasons.append("Агент сообщил о виртуальной среде")
    fingerprint = payload.get("device_fingerprint")
    if fingerprint and any(e["payload"].get("device_fingerprint") == fingerprint for e in previous_scans):
        score += 50
        reasons.append("Такой отпечаток устройства уже встречался")
    return {"score": min(score, 100), "level": "high" if score >= 60 else "review" if score >= 30 else "low",
            "reasons": reasons, "model": "rules-v1"}
