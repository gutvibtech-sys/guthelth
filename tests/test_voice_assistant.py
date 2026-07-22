from voice_assistant import (
    AssistantPrompt,
    ConversationState,
    GutVibeVoiceAssistant,
    KioskPresenceEvent,
    KioskStep,
    SpeechInput,
    SupportedLanguage,
)


class FakeSpeechToText:
    def __init__(self, speech_input):
        self.speech_input = speech_input
        self.language_hints = None

    def transcribe(self, audio, language_hints):
        self.language_hints = language_hints
        return self.speech_input


class FakeTextToSpeech:
    def synthesize(self, prompt):
        return f"audio:{prompt.language}:{prompt.text}".encode("utf-8")


def test_welcome_on_approach_starts_conversation_without_medical_analysis():
    assistant = GutVibeVoiceAssistant()
    state = ConversationState(session_id="session-1")

    prompt = assistant.welcome_on_approach(KioskPresenceEvent(detected=True), state)

    assert prompt is not None
    assert prompt.step == KioskStep.WELCOME
    assert "GutVibe" in prompt.text
    assert state.user_profile_ref is None


def test_voice_input_uses_provider_language_detection_and_advances_flow():
    stt = FakeSpeechToText(SpeechInput("வணக்கம்", SupportedLanguage.TAMIL, 0.98))
    assistant = GutVibeVoiceAssistant(stt_provider=stt)
    state = ConversationState(session_id="session-2")

    prompt = assistant.handle_voice(b"audio", state)

    assert tuple(SupportedLanguage) == stt.language_hints
    assert state.language == SupportedLanguage.TAMIL
    assert prompt.step == KioskStep.CONSENT
    assert "சம்மத" in prompt.text


def test_touch_and_tts_interfaces_are_provider_pluggable():
    assistant = GutVibeVoiceAssistant(tts_provider=FakeTextToSpeech())
    state = ConversationState(session_id="session-3", step=KioskStep.CONSENT)

    prompt = assistant.handle_touch("Continue", state)
    audio = assistant.speak(prompt)

    assert state.consent_confirmed is True
    assert prompt == AssistantPrompt(
        step=KioskStep.REGISTRATION,
        language=SupportedLanguage.ENGLISH,
        text="Please enter your details for registration.",
        ssml="<speak>Please enter your details for registration.</speak>",
        touch_options=("Continue", "Repeat", "Change language"),
    )
    assert audio.startswith(b"audio:")


def test_offline_keyword_language_detector_supports_required_languages():
    assistant = GutVibeVoiceAssistant()

    assert assistant.language_detector.detect("നമസ്കാരം") == SupportedLanguage.MALAYALAM
    assert assistant.language_detector.detect("hello") == SupportedLanguage.ENGLISH
    assert assistant.language_detector.detect("வணக்கம்") == SupportedLanguage.TAMIL
    assert assistant.language_detector.detect("नमस्ते") == SupportedLanguage.HINDI
