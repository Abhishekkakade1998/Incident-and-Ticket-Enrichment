---
doc_id: SOP-PUMP-004
title: Boiler Feed Pump Alarm Response and Troubleshooting Procedure
doc_type: operating_procedure
version: 3.2
effective_date: 2026-02-01
owner: NorthPlant Operations
asset_types: [pump]
alarm_names: [High Bearing Temperature, High Vibration, Low Suction Pressure, Seal Leak Detected, Motor Overcurrent, Low Discharge Flow]
sites: [NorthPlant]
---

# Boiler Feed Pump Alarm Response and Troubleshooting Procedure

## 1. Purpose and Scope

This procedure defines the operator response to alarms on the boiler feed pumps P-101 (Boiler Feed Pump 101) and P-102 (Boiler Feed Pump 102) of the Boiler Feedwater System, Unit 1, NorthPlant. It applies to all shift operators and field technicians. The pumps run as a duty/standby pair taking suction from Deaerator D-110.

## 2. Safety Precautions

Feedwater at the pump suction is at approximately 150 degC. Any leak can flash to steam and cause severe burns. Never approach a leaking mechanical seal without face shield and thermal gloves. Isolation of a boiler feed pump requires a permit to work and the double-block-and-bleed isolation defined in SAF-003.

## 3. Low Suction Pressure

Low suction pressure is the most common initiating alarm on these pumps and frequently precedes cavitation.

1. Check Deaerator D-110 level and pressure on the DCS. A low deaerator level reduces available NPSH.
2. Check the suction strainer differential pressure. A differential pressure above 0.35 bar indicates a blocked strainer; switch to the standby pump and clean the strainer.
3. Verify the suction isolation valve is fully open.
4. Verify the minimum-flow recirculation valve (FV-101 / FV-102) is responding. A sticking recirculation valve at low load is a known cause of recurring low suction pressure.
5. If suction pressure does not recover within 5 minutes, start the standby pump and stop the affected pump.

## 4. High Vibration

1. Confirm the reading with a portable vibration analyser at the drive-end and non-drive-end bearings.
2. If low suction pressure was active in the preceding 15 minutes, treat the vibration as cavitation-induced and correct suction conditions first (Section 3).
3. Vibration above 7.1 mm/s RMS (alarm) requires the area supervisor to be informed. Vibration above 11 mm/s RMS (trip) requires immediate changeover to the standby pump.
4. Record the vibration spectrum. Dominant 1x running speed suggests unbalance, 2x suggests misalignment, broadband high-frequency noise suggests cavitation.

## 5. High Bearing Temperature

1. Verify the temperature on the redundant RTD or a local thermometer.
2. Check lube oil level in the bearing housing sight glass and the oil colour. Milky oil indicates water ingress.
3. Check cooling water flow to the bearing housing.
4. A rise of more than 2 degC per minute, or a temperature above 95 degC, requires changeover to the standby pump.
5. Recurring high bearing temperature that follows high vibration indicates progressive bearing damage; raise a work order for bearing inspection and request lube oil analysis.

## 6. Seal Leak Detected

1. Observe the seal from a safe distance. Do not touch the gland area.
2. If the leak rate exceeds 0.5 l/h, or steam is visible, change over to the standby pump and isolate the affected pump under permit.
3. Raise a P1 or P2 incident depending on whether the standby pump is available (see ESC-001).
4. Mechanical seal damage after cavitation episodes is common; the work order must reference the cavitation history.

## 7. Recurring Alarm Pattern: Cavitation Sequence

When the sequence Low Suction Pressure -> High Vibration -> High Bearing Temperature repeats on the same pump within days, the pump is suffering repeated cavitation. Operators must not simply acknowledge the alarms. The shift supervisor shall raise an incident ticket including the alarm history, the suction strainer DP trend and the recirculation valve position, and request a reliability engineering review.

## 8. Records

Record all actions in the shift log and the incident ticket. Attach the alarm summary for the last 90 days when raising an incident for a recurring alarm.
