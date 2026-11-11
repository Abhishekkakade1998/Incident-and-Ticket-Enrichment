---
doc_id: TG-CMP-012
title: Centrifugal Compressor Surge and High Discharge Temperature Troubleshooting Guide
doc_type: troubleshooting_guide
version: 2.0
effective_date: 2025-11-15
owner: EastRefinery Rotating Equipment
asset_types: [compressor, motor]
alarm_names: [Surge Detected, High Discharge Temperature, High Vibration, Low Lube Oil Pressure, High Interstage Pressure, Motor Overcurrent]
sites: [EastRefinery]
---

# Centrifugal Compressor Surge and High Discharge Temperature Troubleshooting Guide

## Overview

Surge is a flow reversal through a centrifugal compressor that occurs when flow drops below the surge line at the current head. Repeated surge damages thrust bearings, seals and impellers, and overloads the drive motor. This guide applies to Gas Compressor K-501, Recycle Compressor K-502 and Instrument Air Compressor K-503 in Unit 5, EastRefinery.

## Immediate Response to Surge Detected

1. Confirm the anti-surge controller is in AUTO and the recycle (anti-surge) valve is opening. The valve must reach 90% open within 2 seconds of a surge signal.
2. If the controller is in MANUAL, return it to AUTO or open the recycle valve manually to at least 40%.
3. Reduce discharge pressure set point or increase suction flow to move the operating point away from the surge line.
4. Check the drive motor current. Surge commonly produces current swings and Motor Overcurrent alarms on the drive motor (for K-501 this is M-501).
5. If three or more surge events occur within 24 hours, escalate to the rotating equipment engineer and raise an incident ticket (P1 if the machine is the only train in service).

## Diagnosing Recurring Surge

- Slow anti-surge valve: perform a valve stroke test (see KA-0057). A stroke time above 2 seconds is a known root cause.
- Controller margin too small: a surge control margin below 8% has caused recurring surge on K-501.
- Upstream feed gas reduction: coordinate with the upstream unit before rate reductions.
- Fouled suction strainer: check suction strainer differential pressure.

## High Discharge Temperature

High discharge temperature after a surge event is usually caused by hot gas recycle. If it persists:

1. Check aftercooler and intercooler cooling water flow and outlet temperature.
2. Check cooler fouling history; cooling-water-side fouling has caused high discharge temperature on K-501.
3. Reduce compression ratio if the temperature approaches the trip limit of 175 degC.

## High Vibration on Compressors

Check alignment and coupling condition. 1x and 2x running speed components with axial vibration indicate misalignment. Laser alignment is required after any coupling work.

## Information Required in the Incident Ticket

The incident ticket must include the number of surge events in the last 24 hours and 90 days, the anti-surge valve stroke time, controller mode, drive motor current behaviour and any correlated alarms on connected assets.
