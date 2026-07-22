# Phase 6: AI Voice Assistant for GutVibe Wellness Kiosk

Phase 6 introduces a standalone multilingual voice assistant layer for the GutVibe Wellness Kiosk. The layer is intentionally separate from patient storage, face scan processing, skin color analysis, wellness report generation, and doctor referral modules.

## Supported languages

The assistant supports the following language codes and user-facing names:

| Language | Locale |
| --- | --- |
| Malayalam | `ml-IN` |
| English | `en-IN` |
| Tamil | `ta-IN` |
| Hindi | `hi-IN` |

## Interaction model

The kiosk can greet users automatically when a proximity, camera, or other presence sensor emits a detected event. The same conversation state then supports voice and touch input so users can continue by speaking or tapping on-screen controls.

## Kiosk journey

The assistant guides the user through non-clinical workflow steps only:

1. Welcome
2. Consent
3. Registration
4. Face Scan
5. Height & Weight Measurement
6. Wellness Report
7. Doctor Referral
8. Complete

## Provider boundaries

The module defines provider protocols for:

- Speech-to-Text (STT)
- Text-to-Speech (TTS)
- Conversation orchestration

These protocols allow later integrations with cloud AI, local AI, hospital-hosted AI, or accessibility-focused providers without changing the kiosk workflow contract.

## Medical analysis separation

Conversation state stores only session, language, consent, step progress, and an optional external user profile reference. It does not store or interpret face measurements, skin analysis, biomarker values, wellness scores, or diagnosis-related data. Medical analysis modules remain responsible for analysis and reporting.
