# Phase 8: AI Physiological Signal Engine

## Safety boundary

All outputs are numerical **wellness estimates**, not clinical measurements or
diagnoses. Every persisted wellness score and every dashboard/report rendering
must display: **“Wellness Estimate Only – Not a Medical Diagnosis.”** Providers
must not add disease classifications or medical conclusions.

## Components and flow

```text
camera frames ──> CameraSignalProvider ─┐
future rPPG ────> FutureRPPGProvider ───┼─> PhysiologicalEngine ─> SQLite
Bluetooth/watch/sensor readings ────────> SensorSignalProvider ──┘       │
                                                                        ├─ dashboard
                                                                        ├─ wellness PDF
                                                                        ├─ doctor referral
                                                                        └─ WhatsApp summary
```

`SignalProvider` is the common adapter contract. `CameraSignalProvider` and
`SensorSignalProvider` specialize input shape without coupling algorithms or
vendor SDKs to persistence. `FutureRPPGProvider` deliberately raises
`NotImplementedError` until a reviewed camera algorithm is supplied. The engine
offers camera, Bluetooth, smart-watch, and medical-sensor entry points, all of
which route through the registered provider.

## Data model

- `physiological_measurements`: patient, UTC timestamp, provider source, heart
  rate, respiratory rate, HRV placeholder, and confidence.
- `signal_quality`: measurement linkage, patient, timestamp, bounded quality,
  and provider quality label.
- `wellness_scores`: optional biological-age and stress-index placeholders plus
  the immutable safety disclaimer.

Patient ID is the integration key, while this module owns its tables and does
not import registration, face-scan, report, referral, or CRM implementation
details. This preserves independent testing and permits those consumers to read
a latest summary without controlling signal extraction.

## Adding a provider

1. Implement `CameraSignalProvider` or `SensorSignalProvider`.
2. Give it a stable, non-empty `name`.
3. Convert vendor output into `PhysiologicalResult`; confidence and quality must
   be between 0 and 1.
4. Register it with `PhysiologicalEngine.register_provider`.
5. Validate consent, device provenance, calibration, security, and regulatory
   obligations outside this prototype before processing real patient data.
