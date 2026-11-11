---
doc_id: KA-0057
title: "Knowledge Article: Anti-Surge Valve Stroke Test"
doc_type: knowledge_article
version: 1.0
effective_date: 2026-03-02
owner: EastRefinery Instrumentation
asset_types: [compressor]
alarm_names: [Surge Detected]
sites: [EastRefinery]
---

# Anti-Surge Valve Stroke Test

## When to Perform

Perform a stroke test whenever Surge Detected recurs more than twice in a week, after any actuator maintenance, and at every planned shutdown.

## Method

1. Obtain operations approval; the test is performed with the compressor on full recycle or offline.
2. Command the valve from 0% to 100% and record the time to reach 90% open. Acceptance: 2 seconds or less.
3. Command the valve closed and record closing time. Acceptance: 10 seconds or less.
4. Check for overshoot and positioner hunting.

## Known Failure Modes

- Solenoid valve sluggish (replaced on UV-501 in February 2026; stroke time fell from 9 s to 1.8 s).
- Air supply pressure low to the actuator.
- Positioner calibration drift.

## Record

Attach the stroke test record to the incident ticket and to the valve's maintenance history.
