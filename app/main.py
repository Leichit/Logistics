from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.ledger import Ledger
from app.risk import scan_risk

app = FastAPI(title="School Procurement Ledger", version="0.1.0",
              description="Демонстрационный API учёта школьных поставок. Подписи и платежи не подключены.")
ledger = Ledger(os.getenv("LEDGER_DB", "procurement.db"))


@app.get("/")
def index() -> dict[str, Any]:
    return {
        "service": "School Procurement Ledger",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "endpoints": ["POST /tenders", "POST /tenders/{tender_id}/batches",
                      "POST /tenders/{tender_id}/scans", "POST /tenders/{tender_id}/accept",
                      "POST /tenders/{tender_id}/release-payment", "GET /ledger/verify"],
    }


class TenderIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    school: str = Field(min_length=2, max_length=200)
    quantity: int = Field(gt=0, le=100000)
    specifications: dict[str, Any] = Field(default_factory=dict)
    budget: int = Field(gt=0, description="Сумма в минимальных денежных единицах")


class BatchIn(BaseModel):
    quantity: int = Field(gt=0)
    supplier: str = Field(min_length=2, max_length=200)
    device_ids: list[str] = Field(min_length=1)


class ScanIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=120)
    device_fingerprint: str = Field(min_length=64, max_length=64)
    observed_specs: dict[str, Any] = Field(default_factory=dict)
    scan_duration_seconds: float | None = Field(default=None, ge=0)
    virtualized: bool = False
    actor: str = "school-agent"


class ActorIn(BaseModel):
    actor: str = Field(min_length=2, max_length=200)


def event_state(entity_id: str) -> list[dict[str, Any]]:
    return ledger.events(entity_id)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tenders", status_code=201)
def create_tender(body: TenderIn) -> dict[str, Any]:
    tender_id = str(uuid.uuid4())
    ledger.append("TenderCreated", tender_id, "procurement-authority", body.model_dump())
    return {"tender_id": tender_id, "status": "created"}


@app.post("/tenders/{tender_id}/batches", status_code=201)
def register_batch(tender_id: str, body: BatchIn) -> dict[str, Any]:
    events = event_state(tender_id)
    created = next((e for e in events if e["type"] == "TenderCreated"), None)
    if not created:
        raise HTTPException(404, "Тендер не найден")
    if any(e["type"] == "BatchRegistered" for e in events):
        raise HTTPException(409, "Для прототипа разрешена одна партия на тендер")
    if body.quantity != created["payload"]["quantity"] or len(set(body.device_ids)) != len(body.device_ids) or len(body.device_ids) != body.quantity:
        raise HTTPException(422, "Для приёмки партии количество и уникальные device_ids должны точно совпадать с заказом")
    batch_id = str(uuid.uuid4())
    ledger.append("BatchRegistered", tender_id, body.supplier, {"batch_id": batch_id, **body.model_dump()})
    return {"batch_id": batch_id, "status": "registered"}


@app.post("/tenders/{tender_id}/scans", status_code=201)
def record_scan(tender_id: str, body: ScanIn) -> dict[str, Any]:
    events = event_state(tender_id)
    batch = next((e for e in events if e["type"] == "BatchRegistered"), None)
    if not batch:
        raise HTTPException(409, "Сначала зарегистрируйте партию")
    if body.device_id not in batch["payload"]["device_ids"]:
        raise HTTPException(422, "Устройство отсутствует в зарегистрированной партии")
    prior_device_scans = [e for e in events if e["type"] == "ScannedAtSchool" and e["payload"].get("device_id") == body.device_id]
    if prior_device_scans:
        raise HTTPException(409, "Устройство уже сканировалось в рамках этого тендера")
    scan_payload = body.model_dump()
    risk = scan_risk(scan_payload, [e for e in events if e["type"] == "ScannedAtSchool"])
    ledger.append("ScannedAtSchool", tender_id, body.actor, {**scan_payload, "risk": risk})
    return {"status": "recorded", "risk": risk}


@app.post("/tenders/{tender_id}/accept")
def accept_tender(tender_id: str, body: ActorIn) -> dict[str, Any]:
    events = event_state(tender_id)
    created = next((e for e in events if e["type"] == "TenderCreated"), None)
    batch = next((e for e in events if e["type"] == "BatchRegistered"), None)
    if not created or not batch:
        raise HTTPException(409, "Нужны тендер и зарегистрированная партия")
    if any(e["type"] in ("Accepted", "PaymentReleased") for e in events):
        raise HTTPException(409, "Тендер уже принят")
    scans = [e for e in events if e["type"] == "ScannedAtSchool"]
    expected = set(batch["payload"]["device_ids"])
    received = {e["payload"]["device_id"] for e in scans}
    high_risk = [e for e in scans if e["payload"]["risk"]["level"] == "high"]
    if received != expected:
        raise HTTPException(409, f"Приёмка невозможна: проверено {len(received)} из {len(expected)} устройств")
    if high_risk:
        raise HTTPException(409, "Приёмка остановлена: есть устройства с высоким уровнем риска")
    ledger.append("Accepted", tender_id, body.actor, {"quantity": len(received), "status": "accepted"})
    return {"status": "accepted", "quantity": len(received)}


@app.post("/tenders/{tender_id}/release-payment")
def release_payment(tender_id: str, body: ActorIn) -> dict[str, Any]:
    events = event_state(tender_id)
    if not any(e["type"] == "Accepted" for e in events):
        raise HTTPException(409, "Сначала требуется успешная приёмка")
    if any(e["type"] == "PaymentReleased" for e in events):
        raise HTTPException(409, "Выплата уже отмечена")
    created = next(e for e in events if e["type"] == "TenderCreated")
    ledger.append("PaymentReleased", tender_id, body.actor,
                  {"budget": created["payload"]["budget"], "mode": "simulation-only"})
    return {"status": "marked-released", "mode": "simulation-only"}


@app.get("/tenders/{tender_id}/events")
def get_events(tender_id: str) -> list[dict[str, Any]]:
    events = event_state(tender_id)
    if not events:
        raise HTTPException(404, "Тендер не найден")
    return events


@app.get("/ledger/verify")
def verify_ledger() -> dict[str, Any]:
    return ledger.verify()
