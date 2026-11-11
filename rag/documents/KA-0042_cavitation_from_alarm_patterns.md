---
doc_id: KA-0042
title: "Knowledge Article: Recognising Pump Cavitation from Alarm Patterns"
doc_type: knowledge_article
version: 1.1
effective_date: 2026-04-10
owner: Reliability Engineering
asset_types: [pump]
alarm_names: [Low Suction Pressure, High Vibration, High Bearing Temperature, Seal Leak Detected]
sites: [NorthPlant, SouthPlant]
---

# Recognising Pump Cavitation from Alarm Patterns

## Symptom

A centrifugal pump shows a repeating chain of alarms: Low Suction Pressure, followed within 1 to 10 minutes by High Vibration, followed by High Bearing Temperature. The chain recurs every few days and each alarm clears on its own.

## Likely Contributing Factors

1. Insufficient NPSH margin caused by a partially blocked suction strainer. This was the root cause in 4 of 7 historical cases at NorthPlant.
2. Minimum-flow recirculation valve sticking at low load, so the pump runs close to shut-off.
3. Low suction vessel (deaerator) level during load changes.
4. Entrained gas from a faulty deaerator vent.

## Consequences if Untreated

Repeated cavitation erodes the impeller, damages bearings and causes mechanical seal failure. A Seal Leak Detected alarm following a cavitation history should be treated as a consequence, not an independent fault.

## Recommended Actions

- Trend suction strainer differential pressure and clean the strainer if it exceeds 0.35 bar.
- Stroke-test the minimum-flow recirculation valve.
- Request lube oil analysis and a vibration spectrum.
- Raise one incident covering the whole alarm chain rather than separate tickets per alarm, and link historical tickets for the same asset.

## Evidence to Attach

Alarm summary for the last 90 days grouped by alarm name, the correlation between Low Suction Pressure and High Vibration, and the operator recommendations from the alarm management system.
