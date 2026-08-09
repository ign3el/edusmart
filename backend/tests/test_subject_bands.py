"""Subject taxonomy + tutor-persona auto-mapping (services/subject_bands.py).

Added 2026-08-09 alongside the fix for a real gap: the generation prompt had
no instruction telling the model what natural language to write in, so an
Arabic/Hindi document could silently generate English text narrated by an
Arabic/Hindi voice. These tests pin resolve_persona_voice's decision rules,
since that function is the one place "same tutor for Arabic and Islamic
Studies" and "never override an explicit user choice" are actually enforced.
"""
from services.subject_bands import (
    SUBJECT_TAXONOMY,
    SUBJECT_SPECS,
    DEFAULT_VOICE_BY_LANGUAGE,
    resolve_subject_spec,
    resolve_persona_voice,
)


class TestSubjectSpecResolution:
    def test_every_taxonomy_entry_has_a_spec(self):
        for subject in SUBJECT_TAXONOMY:
            assert subject in SUBJECT_SPECS

    def test_known_subject_returns_its_style(self):
        assert "diagram" in resolve_subject_spec("Mathematics")["image_style"]

    def test_unknown_subject_falls_back_to_general_not_error(self):
        assert resolve_subject_spec("Underwater Basket Weaving") == SUBJECT_SPECS["General"]

    def test_none_subject_falls_back_to_general(self):
        assert resolve_subject_spec(None) == SUBJECT_SPECS["General"]

    def test_general_style_is_a_noop_addition(self):
        # Empty string, not some placeholder - _generate_image_unbounded's
        # `f"{grade}. {subject}"` join must not print "None" or similar into
        # a real image prompt.
        assert resolve_subject_spec("General")["image_style"] == ""


class TestPersonaAutoMapping:
    def test_islamic_studies_always_resolves_to_arabic_persona(self):
        # Regardless of detected language OR whatever voice was already
        # picked - explicit product decision, not a default-only nudge.
        assert resolve_persona_voice("Islamic Studies", "en", "af_bella") == "ar_teacher"
        assert resolve_persona_voice("Islamic Studies", "hi", "hm_psi") == "ar_teacher"

    def test_arabic_language_always_resolves_to_arabic_persona(self):
        # Even for a subject that would otherwise map elsewhere.
        assert resolve_persona_voice("Mathematics", "ar", "af_sarah") == "ar_teacher"

    def test_default_voice_gets_refined_by_subject(self):
        # User left the picker at the English language default (af_bella) -
        # once subject is known, refine to the subject-appropriate voice.
        assert resolve_persona_voice("Mathematics", "en", "af_bella") == "af_sarah"
        assert resolve_persona_voice("Science", "en", "af_bella") == "af_nicole"

    def test_explicit_manual_choice_is_never_overridden(self):
        # User picked Fenrir, not the language default (af_bella) - respected
        # even though Mathematics would otherwise map to af_sarah.
        assert resolve_persona_voice("Mathematics", "en", "am_fenrir") == "am_fenrir"

    def test_hindi_default_gets_refined_by_subject(self):
        assert resolve_persona_voice("Mathematics", "hi", "hf_alpha") == "hm_psi"

    def test_unmapped_subject_falls_back_to_current_voice(self):
        # "General"/anything without a language-specific entry keeps
        # whatever was already chosen rather than guessing.
        assert resolve_persona_voice("General", "en", "af_bella") == "af_bella"

    def test_unknown_language_code_falls_back_to_english_mapping(self):
        assert resolve_persona_voice("Mathematics", "fr", "af_bella") == "af_sarah"

    def test_every_default_voice_language_is_mapped(self):
        # DEFAULT_VOICE_BY_LANGUAGE must mirror TeacherCard.jsx's own
        # defaultVoice logic - a language present there with nothing here
        # would silently never get subject refinement.
        for lang in DEFAULT_VOICE_BY_LANGUAGE:
            assert resolve_persona_voice("General", lang, DEFAULT_VOICE_BY_LANGUAGE[lang]) == DEFAULT_VOICE_BY_LANGUAGE[lang]
