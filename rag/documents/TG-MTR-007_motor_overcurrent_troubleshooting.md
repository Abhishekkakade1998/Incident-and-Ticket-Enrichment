---
doc_id: TG-MTR-007
title: Electric Motor Overcurrent and Winding Temperature Troubleshooting
doc_type: troubleshooting_guide
version: 1.4
effective_date: 2025-06-30
owner: Electrical Maintenance
asset_types: [motor, pump, compressor]
alarm_names: [Motor Overcurrent, High Winding Temperature, Motor Trip]
sites: [NorthPlant, SouthPlant, EastRefinery]
---

# Electric Motor Overcurrent and Winding Temperature Troubleshooting

## Motor Overcurrent

A Motor Overcurrent alarm indicates current above 110% of full-load current for longer than the alarm delay.

1. Compare motor current with the driven machine load. For compressor drives, check whether the driven compressor was surging at the time of the alarm; surge-induced current swings are not a motor fault.
2. Check the three phase currents for imbalance greater than 5%, which indicates a supply or winding problem.
3. Inspect the driven equipment for mechanical binding (bearing seizure, rubbing).
4. On start-up, overcurrent lasting less than 20 seconds is normal for direct-on-line starts; for soft-started motors check the ramp time.

## High Winding Temperature

1. Verify RTD readings against a second RTD.
2. Check the cooling air path and filters; blocked filters are the most frequent cause.
3. Check ambient temperature and load. Sustained load above 100% raises winding temperature by roughly 10 degC per 10% overload.

## Motor Trip

Do not reset a tripped motor more than once without electrical maintenance approval. Record the trip relay flags before resetting.

## Escalation

Repeated overcurrent on a criticality-A motor requires a P2 incident and notification of the electrical supervisor. If the overcurrent correlates with driven-equipment process alarms, the incident must be raised against the driven equipment and the motor linked as a related asset.
