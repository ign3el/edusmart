"""Subject-band content specs: visual register and tutor-persona voice per
detected subject, mirroring grade_bands.py's pattern for grade tiers.

SUBJECT_TAXONOMY is a CLOSED list the generation prompt constrains the
model's own "subject" field to (see story_service.py's QUIZ schema) - a free-
text subject can't be reliably mapped to a persona/visual-style, so the model
is told to pick the single closest match from this list rather than invent
its own wording.
"""
from typing import Optional, TypedDict


SUBJECT_TAXONOMY: tuple[str, ...] = (
    "Mathematics",
    "Science",
    "English/Language Arts",
    "Social Studies",
    "Environmental Studies",
    "Islamic Studies",
    "General",
)


class SubjectSpec(TypedDict):
    image_style: str


# Appended AFTER grade_spec's own image_style (see story_service.py's
# _generate_image_unbounded) - grade controls complexity/register, subject
# controls WHAT the illustration actually shows. "General" contributes
# nothing extra; the grade-level register alone already covers it.
SUBJECT_SPECS: dict[str, SubjectSpec] = {
    "Mathematics": {
        "image_style": (
            "include a clear labeled visual aid relevant to the concept - a number line, "
            "bar model, geometric diagram, or annotated equation - rendered like a textbook "
            "figure, not just decorative scenery"
        ),
    },
    "Science": {
        "image_style": (
            "illustrate with scientific accuracy - a labeled diagram, real experiment setup, "
            "or anatomically/ecologically correct depiction - favor an educational-textbook or "
            "field-guide look over whimsical cartoon styling"
        ),
    },
    "English/Language Arts": {
        "image_style": "",  # narrative-illustration register from grade_spec already fits this well
    },
    "Social Studies": {
        "image_style": (
            "ground the scene in a specific, real cultural/historical/geographic setting named "
            "or implied by the document - maps, period-accurate detail, or landmark architecture "
            "where relevant, not a generic backdrop"
        ),
    },
    "Environmental Studies": {
        "image_style": (
            "depict real plants, animals, ecosystems, or civic/environmental settings accurately "
            "and specifically rather than a generic nature backdrop"
        ),
    },
    "Islamic Studies": {
        "image_style": (
            "respectful, non-figurative Islamic visual register - geometric patterns, arabesque "
            "motifs, calligraphy, mosque/architectural elements, modest dress on any people shown. "
            "NEVER depict a prophet or messenger of Islam, in any form, style, or context"
        ),
    },
    "General": {
        "image_style": "",
    },
}


def resolve_subject_spec(subject: Optional[str]) -> SubjectSpec:
    """Look up the image-style addition for a subject, falling back to
    General (a no-op addition) for anything unrecognized - a model that drifts
    from the closed taxonomy should degrade to "no extra guidance", not error."""
    return SUBJECT_SPECS.get((subject or "").strip(), SUBJECT_SPECS["General"])


# ISO 639-1 codes langdetect actually returns for this app's supported
# languages, to a full name for the prompt's LANGUAGE instruction.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ar": "Arabic",
    "hi": "Hindi",
}

# Mirrors TeacherCard.jsx's defaultVoice logic exactly (frontend picks the
# same default before generation, using its OWN language detection at
# extract-text time) - kept as its own constant here, not imported from the
# frontend, so this file has no cross-language coupling to maintain; the two
# lists must be kept in sync by hand if voices are ever added or reassigned.
DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "ar": "ar_teacher",
    "hi": "hf_alpha",
    "en": "af_bella",
}

# subject -> voice_id, per language. Reassigns EXISTING voices by personality
# fit - no new TTS voices/audio required. Only subjects with a language's
# voice roster deep enough to differentiate get an entry; anything else falls
# back to that language's DEFAULT_VOICE_BY_LANGUAGE entry.
SUBJECT_PERSONA_VOICE_MAP: dict[str, dict[str, str]] = {
    "en": {
        "Mathematics": "af_sarah",             # Professional & Clear
        "Science": "af_nicole",                # Energetic & Fun
        "English/Language Arts": "af_bella",   # Warm & Expressive, enthusiastic storyteller
        "Social Studies": "am_michael",        # Wise Narrator, authoritative storytelling
        "Environmental Studies": "af_sky",     # Bright & Cheerful, encourages curiosity
    },
    "hi": {
        "Mathematics": "hm_psi",     # Vikram - Wise Teacher
        "Science": "hm_omega",       # Arjun - Strong Narrator
        "Social Studies": "hf_beta", # Anjali - Warm & Friendly storyteller
        "Environmental Studies": "hf_alpha",  # Priya - Hindi Teacher
    },
    # Arabic has one voice today (ar_teacher / Nour) - every subject maps to
    # it. Kept explicit rather than omitted so resolve_persona_voice's lookup
    # is uniform across languages instead of special-casing "ar" separately.
    "ar": {s: "ar_teacher" for s in SUBJECT_TAXONOMY},
}


def resolve_persona_voice(subject: Optional[str], language_code: Optional[str], current_voice: str) -> str:
    """Decide the tutor persona (voice) a story should narrate with.

    Islamic Studies and Arabic-language documents ALWAYS resolve to the
    Arabic persona (ar_teacher / Nour), regardless of what was picked before
    generation - explicit product decision, not a default-only nudge: the
    same tutor identity for the same cultural/linguistic content every time.

    For every other subject, this only overrides the pre-generation choice
    when the user accepted whatever TeacherCard.jsx pre-selected as the
    language default (DEFAULT_VOICE_BY_LANGUAGE) rather than manually picking
    something else - an explicit manual choice is respected, never silently
    replaced. Subject is only known once the main generation call returns
    (chicken-and-egg: the model decides subject as part of the same call that
    writes the story), so this resolves AFTER generation, before TTS starts.
    """
    lang = (language_code or "en").strip()
    if lang not in SUBJECT_PERSONA_VOICE_MAP:
        lang = "en"

    if subject == "Islamic Studies" or lang == "ar":
        return "ar_teacher"

    default_for_lang = DEFAULT_VOICE_BY_LANGUAGE.get(lang, "af_bella")
    if current_voice != default_for_lang:
        # User made an explicit choice that isn't the language default - respect it.
        return current_voice

    return SUBJECT_PERSONA_VOICE_MAP.get(lang, {}).get(subject or "", current_voice)
