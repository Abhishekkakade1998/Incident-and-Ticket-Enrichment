### Investigation: recurring high-severity alarms on Boiler Feed Pump 101 (last 90 days)

**Affected asset:** Boiler Feed Pump 101 (AST-BFP-101), NorthPlant / Unit 1, criticality A, part of Boiler Feedwater System [T2]

**Alarm:** ALM-007143 Low Suction Pressure - severity high, process alarm, status cleared, raised 2026-11-07T00:02:10Z (value 1.13 barg vs limit 1.2) [T6]

**Priority:** Score 71/100 (P2): high process alarm on criticality-A asset, 15 recurrences in the prior 30 days, status cleared. [T7]

**Alarm history (last 90 days) [T4]:**

- Low Suction Pressure: 42 activations, recurrence rate 0.31, avg ack delay 3.7 min

- High Bearing Temperature: 39 activations, recurrence rate 0.308, avg ack delay 3.4 min

- High Vibration: 38 activations, recurrence rate 0.263, avg ack delay 3.6 min

- Seal Leak Detected: 7 activations, recurrence rate 0.0, avg ack delay 5.3 min

**Trend:** Alarm rate is increasing (57 in the first half of the window vs 69 in the second) [T3]

**Correlated alarms [T5]:**

- Low Suction Pressure -> High Vibration: 21 co-occurrences, confidence 50%

- High Vibration -> High Bearing Temperature: 20 co-occurrences, confidence 53%

- Low Suction Pressure -> High Bearing Temperature: 20 co-occurrences, confidence 48%

**Likely contributing factors** (ranked by agreeing sources):

1. Blocked suction strainer reducing NPSH margin [D1] [D2] [INC-1001] [T8]

2. Cavitation [D2] [D3] [INC-1004] [T5]

3. Low suction vessel / deaerator level [D2] [D5] [INC-1003] [T8]

4. Minimum-flow recirculation valve sticking [D5] [INC-1003] [T8]

5. Lubrication degradation (oil condition / water ingress) [INC-1002]

**Recommended actions:**

1. Check deaerator / suction vessel level and pressure. [T8]

2. Check suction strainer differential pressure; clean if blocked. [T8]

3. Verify suction valve fully open and minimum-flow recirculation in service. [T8]

**Applicable procedure - SOP-PUMP-004, 3. Low Suction Pressure [D2]:**

- Check Deaerator D-110 level and pressure on the DCS. A low deaerator level reduces available NPSH.

- Check the suction strainer differential pressure. A differential pressure above 0.35 bar indicates a blocked strainer; switch to the standby pump and clean the strainer.

- Verify the suction isolation valve is fully open.

- Verify the minimum-flow recirculation valve (FV-101 / FV-102) is responding. A sticking recirculation valve at low load is a known cause of recurring low suction pressure.

- If suction pressure does not recover within 5 minutes, start the standby pump and stop the affected pump.

**Similar historical tickets:**

- [INC-1003] Recurring low suction pressure on BFP-101 (resolved, similarity 0.871) - resolution: Min-flow recirculation valve FV-101 found sticking at 20%. Positioner recalibrated.

- [INC-1001] BFP-101 high vibration and bearing temperature after low suction pressure (closed, similarity 0.647) - resolution: Suction strainer found 60% blocked; cleaned strainer and restored NPSH margin. Vibration back to 2.1 mm/s.

- [INC-1005] BFP-102 low suction pressure while standby pump in service (open, similarity 0.569)

**Open tickets on the asset and correlated assets [T10]:**

- [INC-1005] BFP-102 low suction pressure while standby pump in service - Boiler Feed Pump 102 (open, P2)