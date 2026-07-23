# Phase 11: AI Wellness Kiosk Hardware Integration

## Design goals

The hardware layer is an anti-corruption boundary between GutVibe workflows and
device SDKs. `HardwareManager` exposes normalized measurements and operations;
business modules never import vendor packages. Python structural protocols make
adapters replaceable and permit native implementations on Raspberry Pi, Windows,
Linux, and Android-hosted Python runtimes.

```text
Registration / Face Scan / Wellness / Voice / CRM / Food as Medicine
                              |
                       HardwareManager
                              |
 Camera | Height | Scale | Printer | QR | Speaker | Mic | Payment | Network
                              |
             deployment-specific vendor adapters
```

## Provider contracts

All devices implement health, restart, and diagnostic operations. Specialized
contracts add camera capture, face detection and quality; calibrated height and
stable weight measurements; summary, QR, and consent printing; report and visit
QR generation; audio playback/recording; connectivity status; and future payment
capability discovery. Provider failures are converted into `HardwareError`, so a
workflow can retry or fall back without knowing the underlying SDK.

The payment contract reserves a future UPI adapter boundary. The manager always
raises `PaymentNotEnabledError` for payment creation in Phase 11: it does not
collect credentials, contact a gateway, or move money.

## Persistence and administration

Four SQLite tables separate concerns:

| Table | Purpose |
| --- | --- |
| `hardware_devices` | Enabled adapter inventory and non-secret metadata |
| `hardware_logs` | Timestamped operation and normalized error audit |
| `calibration_history` | Reference, unit, outcome, notes, and time |
| `device_health` | Latest state, message, battery, details, and check time |

The Streamlit hardware dashboard displays camera, printer, scale, height,
network, and battery/UPS state plus the last check. Admin tabs expose inventory,
calibration, restart, diagnostics, and error logs. UI actions call only the
manager facade and therefore remain portable.

## Workflow integration

- **Registration** requests normalized height and stable weight readings.
- **Face scan** requests a photo accepted only with one face and passing quality.
- **Wellness scoring** receives measurements, never raw device handles.
- **Voice assistant** sends/receives audio through speaker and microphone contracts.
- **WhatsApp CRM** and **Food as Medicine** use report/visit QR bytes and printer
  output through the same facade; they contain no printer or QR vendor code.

Adapters are composed in a deployment bootstrap module. A kiosk image can swap a
USB scale for a serial scale by changing registration alone. Secrets belong in a
platform secret store, not `metadata_json` or logs.

## Operations and safety

Health polling should run periodically and before a measurement. Offline devices
must degrade the affected workflow rather than invent a reading. Calibration is
restricted to administrators and every attempt is audited. Logs must exclude
photos, audio, payment data, patient identifiers, and credentials. Device restart
permissions should be constrained by the host OS. Production deployments should
add authentication, encrypted transport/storage, retention controls, monitoring,
and signed adapter packages.
