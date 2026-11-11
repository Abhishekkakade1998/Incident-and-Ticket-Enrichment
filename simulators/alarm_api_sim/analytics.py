"""Pure analytics functions over the alarm list. No I/O, fully unit-testable."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from simulators.alarm_api_sim.data import SEVERITY_ORDER

Alarm = dict[str, Any]


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def filter_alarms(
    alarms: list[Alarm],
    *,
    asset_ids: list[str] | None = None,
    site: str | None = None,
    unit: str | None = None,
    status: list[str] | None = None,
    severity: list[str] | None = None,
    alarm_types: list[str] | None = None,
    alarm_names: list[str] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[Alarm]:
    start_s = iso(start_time) if start_time else None
    end_s = iso(end_time) if end_time else None
    ids = set(asset_ids or [])
    out = []
    for a in alarms:
        if ids and a["asset_id"] not in ids:
            continue
        if site and a["site"].lower() != site.lower():
            continue
        if unit and a["unit"].lower() != unit.lower():
            continue
        if status and a["status"] not in status:
            continue
        if severity and a["severity"] not in severity:
            continue
        if alarm_types and a["alarm_type"] not in alarm_types:
            continue
        if alarm_names and a["alarm_name"].lower() not in {n.lower() for n in alarm_names}:
            continue
        if start_s and a["start_time"] < start_s:
            continue
        if end_s and a["start_time"] > end_s:
            continue
        out.append(a)
    return out


# --------------------------------------------------------------------------- KPIs

SUPPORTED_KPIS = {
    "alarm_count": "Number of alarm activations in scope.",
    "critical_count": "Number of activations with severity=critical.",
    "recurring_rate": "Share of activations that recurred (same asset + alarm) within 24 h of the previous one.",
    "avg_ack_delay": "Mean seconds from activation to operator acknowledgement.",
    "avg_duration": "Mean seconds from activation to clear.",
    "suppression_candidate_rate": "Share of activations that chattered (cleared < 60 s or re-fired within 10 min).",
    "active_count": "Number of activations still active or acknowledged but not cleared.",
}


def _recurrence_flags(alarms: list[Alarm], window: timedelta) -> dict[str, bool]:
    last: dict[tuple[str, str], datetime] = {}
    flags: dict[str, bool] = {}
    for a in sorted(alarms, key=lambda x: x["start_time"]):
        key = (a["asset_id"], a["alarm_name"])
        ts = parse_ts(a["start_time"])
        prev = last.get(key)
        flags[a["alarm_id"]] = prev is not None and ts - prev <= window
        last[key] = ts
    return flags


def compute_kpis(alarms: list[Alarm], kpis: list[str]) -> dict[str, float | int | None]:
    out: dict[str, float | int | None] = {}
    if not alarms:
        return {k: (0 if k.endswith("count") else None) for k in kpis}
    rec24 = _recurrence_flags(alarms, timedelta(hours=24)) if "recurring_rate" in kpis else {}
    rec10 = _recurrence_flags(alarms, timedelta(minutes=10)) if "suppression_candidate_rate" in kpis else {}
    for k in kpis:
        if k == "alarm_count":
            out[k] = len(alarms)
        elif k == "critical_count":
            out[k] = sum(1 for a in alarms if a["severity"] == "critical")
        elif k == "active_count":
            out[k] = sum(1 for a in alarms if a["status"] in ("active", "acknowledged"))
        elif k == "recurring_rate":
            out[k] = round(sum(rec24.values()) / len(alarms), 3)
        elif k == "avg_ack_delay":
            vals = [a["ack_delay_seconds"] for a in alarms if a["ack_delay_seconds"] is not None]
            out[k] = round(mean(vals), 1) if vals else None
        elif k == "avg_duration":
            vals = [a["duration_seconds"] for a in alarms if a["duration_seconds"] is not None]
            out[k] = round(mean(vals), 1) if vals else None
        elif k == "suppression_candidate_rate":
            chatter = sum(1 for a in alarms if rec10.get(a["alarm_id"]) or (a["duration_seconds"] is not None and a["duration_seconds"] < 60))
            out[k] = round(chatter / len(alarms), 3)
    return out


GROUPABLE = {"alarm_name", "asset_id", "asset_name", "severity", "alarm_type", "site", "unit", "status", "priority"}


def summarize(alarms: list[Alarm], group_by: list[str], kpis: list[str]) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], list[Alarm]] = defaultdict(list)
    for a in alarms:
        groups[tuple(a[g] for g in group_by)].append(a)
    rows = []
    for key, items in groups.items():
        row: dict[str, Any] = dict(zip(group_by, key, strict=True))
        row["kpis"] = compute_kpis(items, kpis)
        row["first_seen"] = min(i["start_time"] for i in items)
        row["last_seen"] = max(i["start_time"] for i in items)
        row["_n"] = len(items)
        rows.append(row)
    rows.sort(key=lambda r: (-r["_n"], str(r.get(group_by[0], "")) if group_by else ""))
    for r in rows:
        r.pop("_n")
    return {"groups": rows, "totals": compute_kpis(alarms, kpis), "group_count": len(rows)}


def trends(alarms: list[Alarm], bucket: str, metrics: list[str], start: datetime, end: datetime) -> list[dict[str, Any]]:
    step = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}[bucket]
    if bucket == "hourly":
        cursor = start.replace(minute=0, second=0, microsecond=0)
    else:
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    buckets: dict[str, list[Alarm]] = {}
    keys: list[datetime] = []
    while cursor <= end and len(keys) < 2000:
        keys.append(cursor)
        buckets[iso(cursor)] = []
        cursor += step
    for a in alarms:
        ts = parse_ts(a["start_time"])
        idx = int((ts - keys[0]) / step) if keys else -1
        if 0 <= idx < len(keys):
            buckets[iso(keys[idx])].append(a)
    return [{"bucket_start": k, **compute_kpis(v, metrics)} for k, v in buckets.items()]


def correlate(
    anchors: list[Alarm],
    candidates: list[Alarm],
    lag_minutes: int,
    min_support: int,
    limit: int = 25,
) -> dict[str, Any]:
    """Co-occurrence correlation: B follows A within ``lag_minutes`` (A on an anchor asset)."""
    lag = timedelta(minutes=lag_minutes)
    cand_sorted = sorted(candidates, key=lambda x: x["start_time"])
    cand_ts = [parse_ts(c["start_time"]) for c in cand_sorted]
    key_count = Counter((a["asset_id"], a["alarm_name"]) for a in candidates)
    for a in anchors:
        key_count.setdefault((a["asset_id"], a["alarm_name"]), 0)
    pairs: Counter[tuple[tuple[str, str], tuple[str, str]]] = Counter()
    names: dict[str, str] = {c["asset_id"]: c["asset_name"] for c in candidates + anchors}
    import bisect

    for a in anchors:
        ts = parse_ts(a["start_time"])
        ka = (a["asset_id"], a["alarm_name"])
        lo = bisect.bisect_left(cand_ts, ts)
        hi = bisect.bisect_right(cand_ts, ts + lag)
        seen = set()
        for c in cand_sorted[lo:hi]:
            kb = (c["asset_id"], c["alarm_name"])
            if kb == ka or kb in seen or c["alarm_id"] == a["alarm_id"]:
                continue
            seen.add(kb)
            pairs[(ka, kb)] += 1
    anchor_count = Counter((a["asset_id"], a["alarm_name"]) for a in anchors)
    total = max(1, len(candidates))
    results: list[dict[str, Any]] = []
    for (ka, kb), support in pairs.items():
        if support < min_support:
            continue
        conf = support / max(1, anchor_count[ka])
        p_b = max(1, key_count[kb]) / total
        results.append(
            {
                "leading_alarm": {"asset_id": ka[0], "asset_name": names.get(ka[0]), "alarm_name": ka[1]},
                "following_alarm": {"asset_id": kb[0], "asset_name": names.get(kb[0]), "alarm_name": kb[1]},
                "support": support,
                "confidence": round(min(conf, 1.0), 3),
                "lift": round(conf / p_b, 2),
                "cross_asset": ka[0] != kb[0],
            }
        )
    results.sort(key=lambda r: (-r["support"], -r["confidence"]))
    results = results[:limit]
    anchor_ids = {a["asset_id"] for a in anchors}
    correlated: dict[str, dict[str, Any]] = {}
    for r in results:
        aid = r["following_alarm"]["asset_id"]
        if aid in anchor_ids:
            continue
        entry = correlated.setdefault(aid, {"asset_id": aid, "asset_name": names.get(aid), "total_support": 0})
        entry["total_support"] += r["support"]
    return {
        "correlations": results,
        "correlated_assets": sorted(correlated.values(), key=lambda e: -e["total_support"]),
    }


def flood_windows(alarms: list[Alarm], threshold: int, window_minutes: int) -> list[dict[str, Any]]:
    ordered = sorted(alarms, key=lambda x: x["start_time"])
    ts = [parse_ts(a["start_time"]) for a in ordered]
    win = timedelta(minutes=window_minutes)
    floods: list[dict[str, Any]] = []
    j = 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > win:
            j += 1
        if i - j + 1 >= threshold:
            s, e = ts[j], ts[i]
            if floods and parse_ts(floods[-1]["end"]) >= s:
                floods[-1]["end"] = iso(max(e, parse_ts(floods[-1]["end"])))
                floods[-1]["_members"].update(range(j, i + 1))
            else:
                floods.append({"start": iso(s), "end": iso(e), "_members": set(range(j, i + 1))})
    out = []
    for f in floods:
        members = [ordered[k] for k in sorted(f.pop("_members"))]
        top = Counter(m["alarm_name"] for m in members).most_common(3)
        out.append(
            {
                **f,
                "alarm_count": len(members),
                "peak_rate_per_10min": len(members),
                "top_alarms": [{"alarm_name": n, "count": c} for n, c in top],
                "assets": sorted({m["asset_name"] for m in members}),
            }
        )
    return out


def rationalization_candidates(alarms: list[Alarm], recurrence_threshold: int, stale_minutes: int, anchor: datetime) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[Alarm]] = defaultdict(list)
    for a in alarms:
        by_key[(a["asset_id"], a["alarm_name"])].append(a)
    rec10 = _recurrence_flags(alarms, timedelta(minutes=10))
    out = []
    for (asset_id, name), items in by_key.items():
        reasons = []
        if len(items) >= recurrence_threshold:
            reasons.append("frequent")
        chatter = sum(1 for i in items if rec10.get(i["alarm_id"]) or (i["duration_seconds"] or 999) < 60)
        if chatter >= 3 and chatter / len(items) >= 0.2:
            reasons.append("chattering")
        stale = [
            i for i in items if i["status"] in ("active", "acknowledged", "shelved") and (anchor - parse_ts(i["start_time"])) > timedelta(minutes=stale_minutes)
        ]
        if stale:
            reasons.append("stale")
        if not reasons:
            continue
        out.append(
            {
                "asset_id": asset_id,
                "asset_name": items[0]["asset_name"],
                "alarm_name": name,
                "severity": items[0]["severity"],
                "occurrences": len(items),
                "chatter_count": chatter,
                "stale_count": len(stale),
                "reasons": reasons,
                "recommendation": _rationalization_advice(reasons),
            }
        )
    out.sort(key=lambda c: (-len(c["reasons"]), -c["occurrences"]))
    return out


def _rationalization_advice(reasons: list[str]) -> str:
    if "chattering" in reasons:
        return "Review deadband / on-delay; consider 5-10 s on-delay to remove chatter."
    if "stale" in reasons:
        return "Review standing alarm; confirm it is actionable or shelve with MOC."
    return "Review setpoint against operating envelope; candidate for rationalization workshop."


# --------------------------------------------------------------------------- priority

SEVERITY_WEIGHT = {"critical": 40, "high": 28, "medium": 15, "low": 5}
TYPE_WEIGHT = {"safety": 15, "process": 8, "device": 6, "system": 3}
CRITICALITY_WEIGHT = {"A": 20, "B": 10, "C": 4}


def priority_score(alarm: Alarm, asset: dict[str, Any], history: list[Alarm], anchor: datetime) -> dict[str, Any]:
    recent = [
        h
        for h in history
        if h["alarm_name"] == alarm["alarm_name"]
        and timedelta(0) <= parse_ts(alarm["start_time"]) - parse_ts(h["start_time"]) <= timedelta(days=30)
        and h["alarm_id"] != alarm["alarm_id"]
    ]
    factors = {
        "severity": SEVERITY_WEIGHT[alarm["severity"]],
        "alarm_type": TYPE_WEIGHT.get(alarm["alarm_type"], 0),
        "asset_criticality": CRITICALITY_WEIGHT.get(asset.get("criticality", "C"), 0),
        "recurrence_30d": min(15, len(recent)),
        "status": 10 if alarm["status"] == "active" else (5 if alarm["status"] == "acknowledged" else 0),
    }
    score = min(100, sum(factors.values()))
    band = "P1" if score >= 80 else "P2" if score >= 60 else "P3" if score >= 40 else "P4"
    return {
        "alarm_id": alarm["alarm_id"],
        "priority_score": score,
        "priority_band": band,
        "factors": factors,
        "recurrence_count_30d": len(recent),
        "explanation": (
            f"{alarm['severity']} {alarm['alarm_type']} alarm on criticality-{asset.get('criticality')} asset, "
            f"{len(recent)} recurrences in the prior 30 days, status {alarm['status']}."
        ),
    }


# --------------------------------------------------------------------------- recommendations

# alarm_name -> (actions, likely causes, procedure keywords)
RECOMMENDATION_RULES: dict[str, dict[str, list[str]]] = {
    "High Bearing Temperature": {
        "actions": [
            "Verify bearing temperature on local gauge / redundant RTD before acting on the DCS reading.",
            "Check lube oil level, temperature and flow to the bearing housing.",
            "Inspect for concurrent high vibration indicating mechanical damage.",
            "If temperature exceeds trip limit or rises > 2 degC/min, switch to standby pump.",
        ],
        "causes": ["Lubrication degradation", "Bearing wear following cavitation-induced vibration", "Cooling water loss"],
        "keywords": ["bearing temperature", "lubrication", "pump"],
    },
    "High Vibration": {
        "actions": [
            "Confirm vibration with portable analyser; capture spectrum (1x, 2x, blade-pass).",
            "Check suction conditions for cavitation (NPSH margin, suction pressure, strainer DP).",
            "Reduce load / flow if vibration trends upward; prepare standby unit.",
        ],
        "causes": ["Cavitation", "Misalignment", "Bearing defect", "Unbalance"],
        "keywords": ["vibration", "cavitation", "alignment"],
    },
    "Low Suction Pressure": {
        "actions": [
            "Check deaerator / suction vessel level and pressure.",
            "Check suction strainer differential pressure; clean if blocked.",
            "Verify suction valve fully open and minimum-flow recirculation in service.",
        ],
        "causes": ["Low upstream level", "Blocked suction strainer", "Recirculation valve malfunction"],
        "keywords": ["suction pressure", "npsh", "strainer", "cavitation"],
    },
    "Seal Leak Detected": {
        "actions": [
            "Confirm leak visually from a safe distance; treat hot feedwater as a burn hazard.",
            "Isolate and switch to standby pump if leak exceeds seal-leak limit.",
            "Raise mechanical seal replacement work order.",
        ],
        "causes": ["Mechanical seal face damage from dry running or vibration"],
        "keywords": ["mechanical seal", "leak", "isolation"],
    },
    "Surge Detected": {
        "actions": [
            "Verify anti-surge controller is in automatic and recycle valve is responding.",
            "Increase recycle flow / reduce discharge pressure to move away from the surge line.",
            "Check for suction strainer blockage or upstream flow restriction.",
            "Escalate to rotating-equipment engineer if more than 3 surge events in 24 h.",
        ],
        "causes": ["Anti-surge valve sticking", "Low suction flow", "Process upset upstream"],
        "keywords": ["surge", "anti-surge", "compressor", "recycle valve"],
    },
    "High Discharge Temperature": {
        "actions": [
            "Check intercooler / aftercooler performance and cooling water flow.",
            "Check for recycle-induced heating following surge control action.",
            "Reduce compression ratio if temperature approaches trip limit.",
        ],
        "causes": ["Fouled cooler", "Excess recycle after surge", "Valve leakage"],
        "keywords": ["discharge temperature", "cooler", "compressor"],
    },
    "Motor Overcurrent": {
        "actions": [
            "Compare motor current with driven-equipment load; check for process overload.",
            "Inspect for mechanical binding in the driven machine.",
            "Check supply voltage balance on MCC.",
        ],
        "causes": ["Driven-equipment overload", "Mechanical binding", "Voltage imbalance"],
        "keywords": ["motor current", "overload", "motor"],
    },
}
GENERIC_RULE = {
    "actions": [
        "Confirm the alarm against an independent measurement.",
        "Review the alarm response procedure for this alarm and asset class.",
        "Notify the area supervisor if the condition persists beyond the response time.",
    ],
    "causes": ["Instrument fault", "Process deviation"],
    "keywords": ["alarm response"],
}


def recommendations(
    alarm: Alarm,
    asset: dict[str, Any] | None,
    asset_alarms: list[Alarm],
    *,
    include_related: bool,
    include_asset_context: bool,
    include_historical_pattern: bool,
) -> dict[str, Any]:
    rule = RECOMMENDATION_RULES.get(alarm["alarm_name"], GENERIC_RULE)
    sev_conf = {"critical": 0.9, "high": 0.8, "medium": 0.65, "low": 0.5}[alarm["severity"]]
    recs = [{"step": i + 1, "action": act, "confidence": round(sev_conf - i * 0.05, 2)} for i, act in enumerate(rule["actions"])]
    body: dict[str, Any] = {
        "alarm_id": alarm["alarm_id"],
        "alarm_name": alarm["alarm_name"],
        "asset_id": alarm["asset_id"],
        "recommendations": recs,
        "likely_causes": rule["causes"],
        "procedure_keywords": rule["keywords"],
    }
    start = parse_ts(alarm["start_time"])
    if include_related:
        related = [
            {k: a[k] for k in ("alarm_id", "alarm_name", "severity", "start_time", "status")}
            for a in asset_alarms
            if a["alarm_id"] != alarm["alarm_id"] and abs(parse_ts(a["start_time"]) - start) <= timedelta(minutes=30)
        ]
        body["related_alarms"] = related[:20]
    if include_asset_context and asset:
        body["asset_context"] = {k: asset[k] for k in ("asset_name", "asset_type", "site", "unit", "criticality", "parent_system")}
    if include_historical_pattern:
        same = [a for a in asset_alarms if a["alarm_name"] == alarm["alarm_name"] and timedelta(0) <= start - parse_ts(a["start_time"]) <= timedelta(days=90)]
        ts = sorted(parse_ts(a["start_time"]) for a in same)
        gaps = [(b - a).total_seconds() / 3600 for a, b in zip(ts, ts[1:], strict=False)]
        body["historical_pattern"] = {
            "occurrences_90d": len(same),
            "mean_interval_hours": round(mean(gaps), 1) if gaps else None,
            "recurring": len(same) >= 5,
        }
    return body


# --------------------------------------------------------------------------- calculation code

CALCULATIONS: dict[str, dict[str, str]] = {
    "alarm_flood_index": {
        "description": "Share of 10-minute windows with >= 10 alarms (ISA-18.2 flood definition).",
        "code": "windows = rolling(alarms, '10min').count()\nresult = (windows >= 10).mean() * 100",
        "unit": "percent",
    },
    "critical_alarm_density": {
        "description": "Critical alarms per asset per day.",
        "code": "result = count(alarms[severity=='critical']) / (n_assets * n_days)",
        "unit": "alarms/asset/day",
    },
    "operator_response_efficiency": {
        "description": "Share of alarms acknowledged within 5 minutes.",
        "code": "result = mean(ack_delay_seconds <= 300) * 100",
        "unit": "percent",
    },
    "nuisance_alarm_score": {
        "description": "Weighted share of chattering and fleeting alarms (0-100, higher is worse).",
        "code": "result = 100 * (0.6*chatter_rate + 0.4*fleeting_rate)",
        "unit": "score",
    },
}


def calculation_id(calc_type: str, filters: dict[str, Any]) -> str:
    digest = hashlib.sha256(f"{calc_type}|{sorted(filters.items())}".encode()).hexdigest()[:12]
    return f"CALC-{digest}"


def run_calculation(calc_type: str, alarms: list[Alarm], start: datetime, end: datetime) -> float:
    days = max(1.0, (end - start).total_seconds() / 86400)
    if not alarms:
        return 0.0
    if calc_type == "alarm_flood_index":
        windows = flood_windows(alarms, 10, 10)
        flood_minutes = sum((parse_ts(w["end"]) - parse_ts(w["start"])).total_seconds() / 60 + 10 for w in windows)
        return round(100 * flood_minutes / (days * 1440), 3)
    if calc_type == "critical_alarm_density":
        n_assets = max(1, len({a["asset_id"] for a in alarms}))
        return round(sum(1 for a in alarms if a["severity"] == "critical") / (n_assets * days), 4)
    if calc_type == "operator_response_efficiency":
        acked = [a for a in alarms if a["ack_delay_seconds"] is not None]
        return round(100 * sum(1 for a in acked if a["ack_delay_seconds"] <= 300) / max(1, len(acked)), 2)
    if calc_type == "nuisance_alarm_score":
        k = compute_kpis(alarms, ["suppression_candidate_rate"])
        fleeting = sum(1 for a in alarms if (a["duration_seconds"] or 999) < 60) / len(alarms)
        return round(100 * (0.6 * float(k["suppression_candidate_rate"] or 0) + 0.4 * fleeting), 2)
    raise KeyError(calc_type)


KPI_DEFINITIONS = [{"kpi": name, "description": desc, "standard_reference": "ISA-18.2 / EEMUA 191"} for name, desc in SUPPORTED_KPIS.items()] + [
    {"kpi": name, "description": c["description"], "unit": c["unit"], "calculation": True} for name, c in CALCULATIONS.items()
]


def severity_at_least(threshold: str) -> list[str]:
    return [s for s, v in SEVERITY_ORDER.items() if v >= SEVERITY_ORDER[threshold]]
