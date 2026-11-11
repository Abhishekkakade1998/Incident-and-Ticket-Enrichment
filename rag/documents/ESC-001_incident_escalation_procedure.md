---
doc_id: ESC-001
title: Alarm Incident Classification and Escalation Procedure
doc_type: escalation_procedure
version: 4.0
effective_date: 2026-01-15
owner: Operations Excellence
asset_types: [pump, compressor, motor, turbine, vessel, heat_exchanger, skid, control_system]
alarm_names: []
sites: [NorthPlant, SouthPlant, EastRefinery]
---

# Alarm Incident Classification and Escalation Procedure

## Incident Priority Definitions

- P1 (Critical): Safety alarm active on a criticality-A asset, loss of a duty/standby pair, or risk of unplanned shutdown. Response within 15 minutes. Notify shift superintendent and on-call engineer by phone.
- P2 (High): High-severity alarm recurring on a criticality-A or B asset, or a critical alarm with a standby available. Response within 1 hour. Notify area supervisor.
- P3 (Medium): Recurring medium-severity alarm, or a high-severity alarm on a criticality-C asset. Response within 1 working day.
- P4 (Low): Nuisance or standing alarms; route to the alarm rationalization backlog.

## When an Incident Must Be Raised

An incident ticket must be raised when any of the following is true: a high or critical alarm recurs three or more times in 30 days on the same asset; a safety alarm activates; correlated alarms appear on connected assets; or an operator recommendation requires maintenance work.

## Required Ticket Content

Every alarm incident ticket must contain:

1. Affected asset (name, ID, site, unit, criticality).
2. Triggering alarm(s) with IDs and timestamps.
3. Summary of alarm history (last 90 days) and recurrence.
4. Likely cause, stated as a hypothesis with evidence.
5. Recommended actions and the applicable procedure reference.
6. Links to similar historical tickets and their resolutions.
7. Correlated assets and any open tickets on them.

## Assignment Groups

Pumps, compressors and turbines: Rotating Equipment. Motors and electrical: Electrical Maintenance. Instrumentation and control systems: Instrumentation. Vessels and heat exchangers: Reliability.

## Approval

Tickets prepared with assistance from automated tools must be reviewed and explicitly approved by a qualified operator or engineer before submission.
