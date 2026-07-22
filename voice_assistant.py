"""Multilingual AI voice assistant interfaces for the GutVibe kiosk.

This module is intentionally separated from face, skin, report, and referral
analysis modules. It only manages kiosk conversation state, language routing,
and provider-agnostic speech/text interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol, Sequence


class SupportedLanguage(str, Enum):
    """Languages supported by the kiosk voice assistant."""

    MALAYALAM = "ml-IN"
    ENGLISH = "en-IN"
    TAMIL = "ta-IN"
    HINDI = "hi-IN"


LANGUAGE_NAMES: Mapping[SupportedLanguage, str] = {
    SupportedLanguage.MALAYALAM: "Malayalam",
    SupportedLanguage.ENGLISH: "English",
    SupportedLanguage.TAMIL: "Tamil",
    SupportedLanguage.HINDI: "Hindi",
}


class KioskStep(str, Enum):
    """Non-clinical kiosk journey steps handled by the assistant."""

    WELCOME = "welcome"
    CONSENT = "consent"
    REGISTRATION = "registration"
    FACE_SCAN = "face_scan"
    HEIGHT_WEIGHT = "height_weight"
    WELLNESS_REPORT = "wellness_report"
    DOCTOR_REFERRAL = "doctor_referral"
    COMPLETE = "complete"


STEP_SEQUENCE: tuple[KioskStep, ...] = (
    KioskStep.WELCOME,
    KioskStep.CONSENT,
    KioskStep.REGISTRATION,
    KioskStep.FACE_SCAN,
    KioskStep.HEIGHT_WEIGHT,
    KioskStep.WELLNESS_REPORT,
    KioskStep.DOCTOR_REFERRAL,
    KioskStep.COMPLETE,
)


@dataclass(frozen=True)
class SpeechInput:
    """Provider-normalized speech-to-text result."""

    transcript: str
    language: SupportedLanguage
    confidence: float


@dataclass(frozen=True)
class AssistantPrompt:
    """Text and speech output for one assistant turn."""

    step: KioskStep
    language: SupportedLanguage
    text: str
    ssml: str | None = None
    touch_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class KioskPresenceEvent:
    """Signal emitted when a person approaches the kiosk."""

    detected: bool
    source: str = "proximity_sensor"


class SpeechToTextProvider(Protocol):
    """Interface for pluggable speech-to-text providers."""

    def transcribe(self, audio: bytes, language_hints: Sequence[SupportedLanguage]) -> SpeechInput:
        """Convert audio bytes into text with automatic language detection."""


class TextToSpeechProvider(Protocol):
    """Interface for pluggable text-to-speech providers."""

    def synthesize(self, prompt: AssistantPrompt) -> bytes:
        """Convert assistant text or SSML into playable audio bytes."""


class ConversationProvider(Protocol):
    """Interface for provider-specific conversational AI implementations."""

    def respond(self, state: "ConversationState", user_text: str | None = None) -> AssistantPrompt:
        """Return the next non-medical assistant prompt for the current state."""


@dataclass
class ConversationState:
    """Conversation state kept separate from medical analysis data."""

    session_id: str
    step: KioskStep = KioskStep.WELCOME
    language: SupportedLanguage = SupportedLanguage.ENGLISH
    consent_confirmed: bool = False
    completed_steps: list[KioskStep] = field(default_factory=list)
    user_profile_ref: str | None = None

    def advance(self) -> KioskStep:
        """Move to the next kiosk step without reading medical measurements."""
        if self.step not in self.completed_steps:
            self.completed_steps.append(self.step)
        index = STEP_SEQUENCE.index(self.step)
        self.step = STEP_SEQUENCE[min(index + 1, len(STEP_SEQUENCE) - 1)]
        return self.step


class KeywordLanguageDetector:
    """Small offline detector that can be replaced by a provider later."""

    _keywords: Mapping[SupportedLanguage, tuple[str, ...]] = {
        SupportedLanguage.MALAYALAM: ("നമസ്കാരം", "സമ്മതം", "അതെ", "മലയാളം"),
        SupportedLanguage.ENGLISH: ("hello", "yes", "consent", "english"),
        SupportedLanguage.TAMIL: ("வணக்கம்", "சம்மதம்", "ஆம்", "தமிழ்"),
        SupportedLanguage.HINDI: ("नमस्ते", "सहमति", "हाँ", "हिंदी"),
    }

    def detect(self, text: str) -> SupportedLanguage:
        """Detect the most likely supported language from recognized text."""
        normalized = text.casefold()
        scores = {
            language: sum(1 for keyword in keywords if keyword.casefold() in normalized)
            for language, keywords in self._keywords.items()
        }
        best_language, best_score = max(scores.items(), key=lambda item: item[1])
        return best_language if best_score > 0 else SupportedLanguage.ENGLISH


class ScriptedConversationProvider:
    """Deterministic multilingual kiosk guide for touch and voice channels."""

    _messages: Mapping[KioskStep, Mapping[SupportedLanguage, str]] = {
        KioskStep.WELCOME: {
            SupportedLanguage.MALAYALAM: "നമസ്കാരം, ഗട്ട്‌വൈബ് വെൽനസ് കിയോസ്കിലേക്ക് സ്വാഗതം.",
            SupportedLanguage.ENGLISH: "Welcome to the GutVibe Wellness Kiosk.",
            SupportedLanguage.TAMIL: "வணக்கம், GutVibe நலக் கியோஸ்க்கு வரவேற்கிறோம்.",
            SupportedLanguage.HINDI: "नमस्ते, गटवाइब वेलनेस कियोस्क में आपका स्वागत है.",
        },
        KioskStep.CONSENT: {
            SupportedLanguage.MALAYALAM: "തുടരാൻ നിങ്ങളുടെ സമ്മതം സ്ഥിരീകരിക്കുക.",
            SupportedLanguage.ENGLISH: "Please confirm your consent to continue.",
            SupportedLanguage.TAMIL: "தொடர உங்கள் சம்மதத்தை உறுதிப்படுத்தவும்.",
            SupportedLanguage.HINDI: "आगे बढ़ने के लिए कृपया अपनी सहमति पुष्टि करें.",
        },
        KioskStep.REGISTRATION: {
            SupportedLanguage.MALAYALAM: "രജിസ്ട്രേഷനായി നിങ്ങളുടെ വിവരങ്ങൾ നൽകുക.",
            SupportedLanguage.ENGLISH: "Please enter your details for registration.",
            SupportedLanguage.TAMIL: "பதிவுக்காக உங்கள் விவரங்களை உள்ளிடவும்.",
            SupportedLanguage.HINDI: "पंजीकरण के लिए कृपया अपना विवरण दर्ज करें.",
        },
        KioskStep.FACE_SCAN: {
            SupportedLanguage.MALAYALAM: "ഫേസ് സ്കാനിനായി ക്യാമറയിലേക്ക് നോക്കുക.",
            SupportedLanguage.ENGLISH: "Please look at the camera for the face scan.",
            SupportedLanguage.TAMIL: "முக ஸ்கேனுக்காக கேமராவை நோக்கிப் பாருங்கள்.",
            SupportedLanguage.HINDI: "फेस स्कैन के लिए कृपया कैमरे की ओर देखें.",
        },
        KioskStep.HEIGHT_WEIGHT: {
            SupportedLanguage.MALAYALAM: "ഉയരവും ഭാരവും അളക്കാൻ പ്ലാറ്റ്ഫോമിൽ നിൽക്കുക.",
            SupportedLanguage.ENGLISH: "Please stand on the platform for height and weight measurement.",
            SupportedLanguage.TAMIL: "உயரமும் எடையும் அளவிட மேடையில் நிற்கவும்.",
            SupportedLanguage.HINDI: "ऊंचाई और वजन मापने के लिए कृपया प्लेटफॉर्म पर खड़े हों.",
        },
        KioskStep.WELLNESS_REPORT: {
            SupportedLanguage.MALAYALAM: "നിങ്ങളുടെ വെൽനസ് റിപ്പോർട്ട് തയ്യാറാക്കുന്നു.",
            SupportedLanguage.ENGLISH: "Your wellness report is being prepared.",
            SupportedLanguage.TAMIL: "உங்கள் நல அறிக்கை தயாராகிறது.",
            SupportedLanguage.HINDI: "आपकी वेलनेस रिपोर्ट तैयार की जा रही है.",
        },
        KioskStep.DOCTOR_REFERRAL: {
            SupportedLanguage.MALAYALAM: "ഡോക്ടർ റഫറൽ വേണമെങ്കിൽ തിരഞ്ഞെടുക്കുക.",
            SupportedLanguage.ENGLISH: "Choose whether you would like a doctor referral.",
            SupportedLanguage.TAMIL: "மருத்துவர் பரிந்துரை வேண்டுமா என்பதைத் தேர்ந்தெடுக்கவும்.",
            SupportedLanguage.HINDI: "कृपया चुनें कि क्या आपको डॉक्टर रेफरल चाहिए.",
        },
        KioskStep.COMPLETE: {
            SupportedLanguage.MALAYALAM: "നന്ദി. നിങ്ങളുടെ സന്ദർശനം പൂർത്തിയായി.",
            SupportedLanguage.ENGLISH: "Thank you. Your kiosk visit is complete.",
            SupportedLanguage.TAMIL: "நன்றி. உங்கள் கியோஸ்க் வருகை முடிந்தது.",
            SupportedLanguage.HINDI: "धन्यवाद. आपकी कियोस्क यात्रा पूरी हो गई है.",
        },
    }

    def respond(self, state: ConversationState, user_text: str | None = None) -> AssistantPrompt:
        text = self._messages[state.step][state.language]
        return AssistantPrompt(
            step=state.step,
            language=state.language,
            text=text,
            ssml=f"<speak>{text}</speak>",
            touch_options=("Continue", "Repeat", "Change language"),
        )


class GutVibeVoiceAssistant:
    """Coordinates presence, STT, TTS, touch, and conversation providers."""

    def __init__(
        self,
        conversation_provider: ConversationProvider | None = None,
        language_detector: KeywordLanguageDetector | None = None,
        stt_provider: SpeechToTextProvider | None = None,
        tts_provider: TextToSpeechProvider | None = None,
    ) -> None:
        self.conversation_provider = conversation_provider or ScriptedConversationProvider()
        self.language_detector = language_detector or KeywordLanguageDetector()
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider

    def welcome_on_approach(self, event: KioskPresenceEvent, state: ConversationState) -> AssistantPrompt | None:
        """Automatically greet a user when proximity/presence is detected."""
        if not event.detected:
            return None
        state.step = KioskStep.WELCOME
        return self.conversation_provider.respond(state)

    def handle_voice(self, audio: bytes, state: ConversationState) -> AssistantPrompt:
        """Process voice input, auto-detect language, and return the next prompt."""
        if self.stt_provider is None:
            raise RuntimeError("A speech-to-text provider must be configured for voice input.")
        speech = self.stt_provider.transcribe(audio, tuple(SupportedLanguage))
        state.language = speech.language or self.language_detector.detect(speech.transcript)
        state.advance()
        return self.conversation_provider.respond(state, speech.transcript)

    def handle_touch(self, action: str, state: ConversationState) -> AssistantPrompt:
        """Process a touch-screen action through the same conversation flow."""
        if action.casefold() in {"continue", "yes", "confirm"}:
            if state.step == KioskStep.CONSENT:
                state.consent_confirmed = True
            state.advance()
        return self.conversation_provider.respond(state, action)

    def speak(self, prompt: AssistantPrompt) -> bytes:
        """Render a prompt through the configured text-to-speech provider."""
        if self.tts_provider is None:
            raise RuntimeError("A text-to-speech provider must be configured for audio output.")
        return self.tts_provider.synthesize(prompt)
