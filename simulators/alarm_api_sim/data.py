"""Deterministic, seeded alarm dataset for the Alarm Management API simulator.

The dataset is synthetic but shaped to contain the patterns the copilot is meant
to discover:

* Boiler Feed Pump 101 suffers recurring cavitation episodes: *Low Suction
  Pressure* is followed within minutes by *High Vibration* and *High Bearing
  Temperature* (correlation + recurrence).
* Gas Compressor K-501 surges; *Surge Detected* co-occurs with *High Discharge
  Temperature* on K-501 and *Motor Overcurrent* on its drive motor M-501 (a
  cross-asset correlation used by "open tickets on correlated assets").
* Unit 2 has occasional alarm floods.
* A handful of alarms are still *active* at the anchor time, including
  high/critical ones in EastRefinery, so "highest-priority active alarm" works.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_TO_PRIORITY = {"critical": 1, "high": 2, "medium": 3, "low": 4}

ASSETS: list[dict[str, Any]] = [
    # NorthPlant
    {
        "asset_id": "AST-BFP-101",
        "asset_name": "Boiler Feed Pump 101",
        "asset_type": "pump",
        "site": "NorthPlant",
        "unit": "Unit 1",
        "tag": "P-101",
        "criticality": "A",
        "manufacturer": "Sulzer",
        "model": "HPcp 200",
        "parent_system": "Boiler Feedwater System",
        "connected": ["AST-DEA-110", "AST-BFP-102"],
    },
    {
        "asset_id": "AST-BFP-102",
        "asset_name": "Boiler Feed Pump 102",
        "asset_type": "pump",
        "site": "NorthPlant",
        "unit": "Unit 1",
        "tag": "P-102",
        "criticality": "A",
        "manufacturer": "Sulzer",
        "model": "HPcp 200",
        "parent_system": "Boiler Feedwater System",
        "connected": ["AST-DEA-110", "AST-BFP-101"],
    },
    {
        "asset_id": "AST-DEA-110",
        "asset_name": "Deaerator D-110",
        "asset_type": "vessel",
        "site": "NorthPlant",
        "unit": "Unit 1",
        "tag": "D-110",
        "criticality": "B",
        "manufacturer": "Stork",
        "model": "DA-40",
        "parent_system": "Boiler Feedwater System",
        "connected": ["AST-BFP-101", "AST-BFP-102"],
    },
    {
        "asset_id": "AST-CWP-201",
        "asset_name": "Cooling Water Pump 201",
        "asset_type": "pump",
        "site": "NorthPlant",
        "unit": "Unit 2",
        "tag": "P-201",
        "criticality": "B",
        "manufacturer": "KSB",
        "model": "Omega 300",
        "parent_system": "Cooling Water System",
        "connected": ["AST-HEX-210"],
    },
    {
        "asset_id": "AST-HEX-210",
        "asset_name": "Heat Exchanger E-210",
        "asset_type": "heat_exchanger",
        "site": "NorthPlant",
        "unit": "Unit 2",
        "tag": "E-210",
        "criticality": "B",
        "manufacturer": "Alfa Laval",
        "model": "T20",
        "parent_system": "Cooling Water System",
        "connected": ["AST-CWP-201", "AST-CDP-202"],
    },
    {
        "asset_id": "AST-CDP-202",
        "asset_name": "Condensate Pump 202",
        "asset_type": "pump",
        "site": "NorthPlant",
        "unit": "Unit 2",
        "tag": "P-202",
        "criticality": "C",
        "manufacturer": "Grundfos",
        "model": "CR 95",
        "parent_system": "Condensate System",
        "connected": ["AST-HEX-210"],
    },
    # SouthPlant
    {
        "asset_id": "AST-STT-301",
        "asset_name": "Steam Turbine T-301",
        "asset_type": "turbine",
        "site": "SouthPlant",
        "unit": "Unit 3",
        "tag": "T-301",
        "criticality": "A",
        "manufacturer": "Siemens",
        "model": "SST-300",
        "parent_system": "Power Generation",
        "connected": ["AST-LOP-302"],
    },
    {
        "asset_id": "AST-LOP-302",
        "asset_name": "Lube Oil Pump 302",
        "asset_type": "pump",
        "site": "SouthPlant",
        "unit": "Unit 3",
        "tag": "P-302",
        "criticality": "B",
        "manufacturer": "Viking",
        "model": "LQ-32",
        "parent_system": "Turbine Lube Oil System",
        "connected": ["AST-STT-301"],
    },
    {
        "asset_id": "AST-FGS-401",
        "asset_name": "Fuel Gas Skid FG-401",
        "asset_type": "skid",
        "site": "SouthPlant",
        "unit": "Unit 4",
        "tag": "FG-401",
        "criticality": "A",
        "manufacturer": "Emerson",
        "model": "FGS-2",
        "parent_system": "Fuel Gas System",
        "connected": ["AST-BMS-402"],
    },
    {
        "asset_id": "AST-BMS-402",
        "asset_name": "Burner Management System BMS-402",
        "asset_type": "control_system",
        "site": "SouthPlant",
        "unit": "Unit 4",
        "tag": "BMS-402",
        "criticality": "A",
        "manufacturer": "Honeywell",
        "model": "BMS-X",
        "parent_system": "Fired Heater H-400",
        "connected": ["AST-FGS-401"],
    },
    # EastRefinery
    {
        "asset_id": "AST-CMP-501",
        "asset_name": "Gas Compressor K-501",
        "asset_type": "compressor",
        "site": "EastRefinery",
        "unit": "Unit 5",
        "tag": "K-501",
        "criticality": "A",
        "manufacturer": "Atlas Copco",
        "model": "GT-050",
        "parent_system": "Wet Gas Compression",
        "connected": ["AST-MTR-501", "AST-CMP-502"],
    },
    {
        "asset_id": "AST-CMP-502",
        "asset_name": "Recycle Compressor K-502",
        "asset_type": "compressor",
        "site": "EastRefinery",
        "unit": "Unit 5",
        "tag": "K-502",
        "criticality": "A",
        "manufacturer": "Elliott",
        "model": "EDGE 25",
        "parent_system": "Hydrotreater Recycle Gas",
        "connected": ["AST-MTR-502", "AST-CMP-501"],
    },
    {
        "asset_id": "AST-CMP-503",
        "asset_name": "Instrument Air Compressor K-503",
        "asset_type": "compressor",
        "site": "EastRefinery",
        "unit": "Unit 5",
        "tag": "K-503",
        "criticality": "B",
        "manufacturer": "Ingersoll Rand",
        "model": "Centac C400",
        "parent_system": "Instrument Air",
        "connected": ["AST-MTR-503"],
    },
    {
        "asset_id": "AST-MTR-501",
        "asset_name": "Compressor Drive Motor M-501",
        "asset_type": "motor",
        "site": "EastRefinery",
        "unit": "Unit 5",
        "tag": "M-501",
        "criticality": "A",
        "manufacturer": "ABB",
        "model": "AMI 500",
        "parent_system": "Wet Gas Compression",
        "connected": ["AST-CMP-501"],
    },
    {
        "asset_id": "AST-MTR-502",
        "asset_name": "Recycle Compressor Motor M-502",
        "asset_type": "motor",
        "site": "EastRefinery",
        "unit": "Unit 5",
        "tag": "M-502",
        "criticality": "A",
        "manufacturer": "WEG",
        "model": "W22 HV",
        "parent_system": "Hydrotreater Recycle Gas",
        "connected": ["AST-CMP-502"],
    },
    {
        "asset_id": "AST-MTR-503",
        "asset_name": "Air Compressor Motor M-503",
        "asset_type": "motor",
        "site": "EastRefinery",
        "unit": "Unit 5",
        "tag": "M-503",
        "criticality": "C",
        "manufacturer": "Siemens",
        "model": "SIMOTICS",
        "parent_system": "Instrument Air",
        "connected": ["AST-CMP-503"],
    },
]

# alarm_name -> (alarm_type, severity, unit_of_measure, threshold)
ALARM_TEMPLATES: dict[str, list[tuple[str, str, str, str, float]]] = {
    "pump": [
        ("High Bearing Temperature", "device", "high", "degC", 85.0),
        ("High Vibration", "device", "high", "mm/s", 7.1),
        ("Low Suction Pressure", "process", "high", "barg", 1.2),
        ("Seal Leak Detected", "safety", "critical", "l/h", 0.5),
        ("Motor Overcurrent", "device", "medium", "A", 410.0),
        ("Low Discharge Flow", "process", "medium", "m3/h", 120.0),
    ],
    "compressor": [
        ("High Discharge Temperature", "process", "high", "degC", 165.0),
        ("Surge Detected", "safety", "critical", "%", 10.0),
        ("High Vibration", "device", "high", "mm/s", 7.1),
        ("Low Lube Oil Pressure", "safety", "critical", "barg", 1.5),
        ("High Interstage Pressure", "process", "medium", "barg", 18.0),
    ],
    "motor": [
        ("Motor Overcurrent", "device", "medium", "A", 520.0),
        ("High Winding Temperature", "device", "high", "degC", 130.0),
        ("Motor Trip", "safety", "critical", "state", 1.0),
        ("High Vibration", "device", "high", "mm/s", 4.5),
    ],
    "vessel": [
        ("High Level", "process", "high", "%", 85.0),
        ("Low Level", "process", "high", "%", 15.0),
        ("Low Pressure", "process", "medium", "barg", 0.2),
    ],
    "heat_exchanger": [
        ("High Differential Pressure", "process", "medium", "bar", 1.8),
        ("High Outlet Temperature", "process", "high", "degC", 45.0),
        ("Fouling Indicator", "system", "low", "%", 70.0),
    ],
    "turbine": [
        ("Overspeed", "safety", "critical", "rpm", 3300.0),
        ("High Exhaust Temperature", "process", "high", "degC", 540.0),
        ("High Vibration", "device", "high", "um", 125.0),
        ("Low Lube Oil Pressure", "safety", "critical", "barg", 1.0),
    ],
    "skid": [
        ("Low Fuel Gas Pressure", "process", "high", "barg", 2.0),
        ("Gas Detector Alarm", "safety", "critical", "%LEL", 20.0),
        ("Filter High Differential Pressure", "device", "low", "bar", 0.8),
    ],
    "control_system": [
        ("Flame Failure", "safety", "critical", "state", 1.0),
        ("Controller Communication Fault", "system", "medium", "state", 1.0),
        ("Purge Incomplete", "safety", "high", "state", 1.0),
    ],
}

SITE_ACK_DELAY = {"NorthPlant": 240, "SouthPlant": 520, "EastRefinery": 330}
BASE_RATE_PER_DAY = {"A": 1.6, "B": 1.1, "C": 0.7}
DATA_START = datetime(2026, 1, 1, tzinfo=UTC)
DEFAULT_ANCHOR = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@dataclass
class Dataset:
    assets: dict[str, dict[str, Any]]
    alarms: list[dict[str, Any]]
    alarms_by_id: dict[str, dict[str, Any]]
    anchor: datetime


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _template(asset: dict[str, Any], alarm_name: str) -> tuple[str, str, str, str, float]:
    for t in ALARM_TEMPLATES[asset["asset_type"]]:
        if t[0] == alarm_name:
            return t
    raise KeyError(alarm_name)


class _Builder:
    def __init__(self, anchor: datetime, seed: int) -> None:
        self.rng = random.Random(seed)
        self.anchor = anchor
        self.alarms: list[dict[str, Any]] = []

    def add(
        self,
        asset: dict[str, Any],
        alarm_name: str,
        start: datetime,
        *,
        force_status: str | None = None,
        duration_min: float | None = None,
    ) -> None:
        name, alarm_type, severity, uom, threshold = _template(asset, alarm_name)
        rng = self.rng
        ack_mean = SITE_ACK_DELAY[asset["site"]] * (0.6 if severity == "critical" else 1.0)
        ack_delay = max(5, int(rng.expovariate(1 / ack_mean)))
        dur = duration_min if duration_min is not None else rng.choice([0.5, 2, 5, 12, 25, 45, 90, 240])
        end = start + timedelta(minutes=dur)
        ack_time: datetime | None = start + timedelta(seconds=ack_delay)
        status = "cleared"
        if force_status:
            status = force_status
        elif end > self.anchor:
            status = "acknowledged" if ack_time and ack_time <= self.anchor else "active"
        elif rng.random() < 0.015:
            status = "shelved"
        if status == "active":
            ack_time, end_time = None, None
        elif status in ("acknowledged", "shelved"):
            end_time = None
        else:
            end_time = end
        direction = -1 if alarm_name.startswith("Low") else 1
        value = round(threshold * (1 + direction * rng.uniform(0.03, 0.25)), 2)
        self.alarms.append(
            {
                "asset_id": asset["asset_id"],
                "asset_name": asset["asset_name"],
                "asset_type": asset["asset_type"],
                "site": asset["site"],
                "unit": asset["unit"],
                "tag": f"{asset['tag']}.{''.join(w[0] for w in alarm_name.split()).upper()}",
                "alarm_name": name,
                "alarm_type": alarm_type,
                "severity": severity,
                "priority": SEVERITY_TO_PRIORITY[severity],
                "status": status,
                "start_time": _iso(start),
                "end_time": _iso(end_time) if end_time else None,
                "ack_time": _iso(ack_time) if ack_time else None,
                "ack_delay_seconds": ack_delay if ack_time else None,
                "duration_seconds": int(dur * 60) if end_time else None,
                "value": value,
                "threshold": threshold,
                "unit_of_measure": uom,
                "description": f"{name} on {asset['asset_name']} ({value} {uom}, limit {threshold} {uom})",
            }
        )


def build_dataset(anchor: datetime | None = None, seed: int = 20260511) -> Dataset:
    anchor = (anchor or DEFAULT_ANCHOR).astimezone(UTC)
    start = min(DATA_START, anchor - timedelta(days=300))
    b = _Builder(anchor, seed)
    rng = b.rng
    assets = {a["asset_id"]: a for a in ASSETS}

    # 1) Background noise for every asset.
    days = (anchor - start).days
    for asset in ASSETS:
        rate = BASE_RATE_PER_DAY[asset["criticality"]]
        templates = ALARM_TEMPLATES[asset["asset_type"]]
        weights = [{"low": 5, "medium": 4, "high": 2.5, "critical": 0.6}[t[2]] for t in templates]
        for d in range(days):
            day = start + timedelta(days=d)
            n = sum(1 for _ in range(4) if rng.random() < rate / 4)
            for _ in range(n):
                tmpl = rng.choices(templates, weights=weights)[0]
                b.add(asset, tmpl[0], day + timedelta(minutes=rng.randint(0, 1439)))

    # 2) BFP-101 cavitation episodes: recurring, correlated, high severity.
    bfp = assets["AST-BFP-101"]
    t = start + timedelta(days=2, hours=3)
    while t < anchor - timedelta(hours=6):
        b.add(bfp, "Low Suction Pressure", t, duration_min=rng.uniform(4, 20))
        b.add(bfp, "High Vibration", t + timedelta(minutes=rng.uniform(1, 6)), duration_min=rng.uniform(5, 30))
        if rng.random() < 0.8:
            b.add(bfp, "High Bearing Temperature", t + timedelta(minutes=rng.uniform(6, 14)), duration_min=rng.uniform(10, 60))
        if rng.random() < 0.15:
            b.add(bfp, "Seal Leak Detected", t + timedelta(minutes=rng.uniform(15, 40)), duration_min=rng.uniform(30, 120))
        t += timedelta(days=rng.uniform(2.5, 5.5), minutes=rng.randint(0, 600))

    # 3) K-501 surge episodes with cross-asset effect on M-501.
    k501, m501 = assets["AST-CMP-501"], assets["AST-MTR-501"]
    t = start + timedelta(days=5, hours=9)
    while t < anchor - timedelta(hours=6):
        b.add(k501, "Surge Detected", t, duration_min=rng.uniform(1, 8))
        b.add(k501, "High Discharge Temperature", t + timedelta(minutes=rng.uniform(2, 9)), duration_min=rng.uniform(10, 40))
        b.add(m501, "Motor Overcurrent", t + timedelta(minutes=rng.uniform(0, 4)), duration_min=rng.uniform(1, 6))
        t += timedelta(days=rng.uniform(6, 11), minutes=rng.randint(0, 900))

    # 4) Unit 2 floods: bursts of alarms within ten minutes.
    unit2 = [a for a in ASSETS if a["unit"] == "Unit 2"]
    t = start + timedelta(days=20)
    while t < anchor:
        for _ in range(rng.randint(12, 18)):
            a = rng.choice(unit2)
            name = rng.choice(ALARM_TEMPLATES[a["asset_type"]])[0]
            b.add(a, name, t + timedelta(seconds=rng.randint(0, 540)), duration_min=rng.uniform(0.3, 8))
        t += timedelta(days=rng.uniform(18, 30))

    # 5) Guaranteed active alarms at anchor (deterministic demo state).
    active = [
        ("AST-CMP-501", "Surge Detected", 35, "active"),
        ("AST-CMP-501", "High Discharge Temperature", 28, "active"),
        ("AST-MTR-501", "Motor Overcurrent", 33, "acknowledged"),
        ("AST-CMP-502", "High Vibration", 75, "active"),
        ("AST-CMP-503", "High Interstage Pressure", 140, "active"),
        ("AST-BFP-101", "High Bearing Temperature", 50, "active"),
        ("AST-BFP-102", "Low Suction Pressure", 20, "active"),
        ("AST-BFP-102", "Motor Overcurrent", 95, "acknowledged"),
        ("AST-HEX-210", "Fouling Indicator", 400, "active"),
        ("AST-STT-301", "High Exhaust Temperature", 65, "active"),
    ]
    for asset_id, name, minutes_ago, status in active:
        b.add(assets[asset_id], name, anchor - timedelta(minutes=minutes_ago), force_status=status, duration_min=minutes_ago + 30)

    anchor_iso = _iso(anchor)
    b.alarms = [a for a in b.alarms if a["start_time"] <= anchor_iso]
    b.alarms.sort(key=lambda a: a["start_time"])
    for i, alarm in enumerate(b.alarms, start=1):
        alarm["alarm_id"] = f"ALM-{i:06d}"
    # Put alarm_id first for readability.
    alarms = [{"alarm_id": a.pop("alarm_id"), **a} for a in b.alarms]
    return Dataset(assets=assets, alarms=alarms, alarms_by_id={a["alarm_id"]: a for a in alarms}, anchor=anchor)


def asset_metadata(asset: dict[str, Any], anchor: datetime) -> dict[str, Any]:
    """Expanded metadata view (GET /assets/{id}/metadata)."""
    seed = sum(ord(c) for c in asset["asset_id"])
    install_year = 2008 + seed % 14
    last_service = anchor - timedelta(days=20 + seed % 160)
    limits = {t[0]: {"threshold": t[4], "unit_of_measure": t[3], "severity": t[2]} for t in ALARM_TEMPLATES[asset["asset_type"]]}
    return {
        "asset_id": asset["asset_id"],
        "asset_name": asset["asset_name"],
        "asset_type": asset["asset_type"],
        "site": asset["site"],
        "unit": asset["unit"],
        "tag": asset["tag"],
        "criticality": asset["criticality"],
        "manufacturer": asset["manufacturer"],
        "model": asset["model"],
        "install_date": f"{install_year}-0{1 + seed % 9}-15",
        "parent_system": asset["parent_system"],
        "connected_assets": asset["connected"],
        "maintenance": {
            "last_service_date": last_service.date().isoformat(),
            "service_interval_days": 180 if asset["criticality"] == "A" else 365,
            "open_work_orders": seed % 3,
        },
        "operating_limits": limits,
        "status": "in_service",
    }
