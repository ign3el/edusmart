"""Grade-band content specs shared by story text, image, and TTS generation.

Single source of truth for "what does age-appropriate mean at this grade" -
every prompt-building site (story_service.py) reads from here instead of
hardcoding its own grade logic. Grades are grouped into four tiers rather
than given 12 hand-written specs each, since the pedagogical difference
between adjacent grades (e.g. Grade 6 vs Grade 7) is negligible next to the
difference between tiers (e.g. KG-2 vs Grade 6).

GRADE_IDS matches the frontend select's `value` exactly - the id sent in the
`grade_level` form field IS the key here, not a free-text label. This is the
fix for the old bug where the backend received a bare digit ("3") with no
tier information at all.
"""
from typing import TypedDict


class TierSpec(TypedDict):
    vocabulary: str
    sentence_style: str
    narrative_length: str
    quiz_cognitive_level: str
    image_style: str
    tts_speed: float
    # Machine-checkable band for the mean words-per-sentence of narrative_text.
    # Everything else in this table is prose aimed at the model; these two exist
    # so the output can actually be MEASURED against the spec instead of trusted.
    # Measured 2026-08-03: a grade-10 generation was indistinguishable from a
    # grade-5 one - the prose spec was delivered to the model and ignored, and
    # nothing downstream noticed.
    #
    # Bands are deliberately WIDER than the prose sentence_style above. They
    # exist to catch a whole tier of drift ("this grade-10 story reads like it
    # is for 9-year-olds"), not to police individual sentences, and a false
    # rejection is expensive: it costs the user a credit and a regeneration.
    min_avg_sentence_words: float
    max_avg_sentence_words: float
    # Bare-recall question openers are a failure at upper tiers, where the spec
    # asks for comparison/justification/evaluation rather than lookup.
    forbid_bare_recall: bool


TIER_SPECS: dict[str, TierSpec] = {
    "early": {  # KG-1, KG-2, Grade 1, Grade 2 (ages ~3-7)
        "vocabulary": (
            "Use only very simple, everyday words a 3-7 year old already knows. "
            "Avoid multi-syllable or abstract words; if a new word is essential, "
            "explain it immediately in the same sentence using a familiar comparison."
        ),
        "sentence_style": "Short, simple sentences of 6-10 words. One idea per sentence. No compound or complex sentences.",
        "narrative_length": "2-4 short sentences",
        "quiz_cognitive_level": (
            "Remember/Identify only - direct recall and simple matching questions "
            "(\"What color was the ball?\"). No inference, no multi-step reasoning, "
            "no abstract comparisons."
        ),
        "image_style": "extra-simple flat shapes, bold bright primary colors, one large friendly character per scene, minimal or no background clutter",
        "tts_speed": 0.85,
        "min_avg_sentence_words": 3.0,
        "max_avg_sentence_words": 12.0,
        "forbid_bare_recall": False,
    },
    "lower": {  # Grade 3, 4, 5 (ages ~7-10)
        "vocabulary": (
            "Use everyday and grade-appropriate academic words. Explain any "
            "technical or subject-specific term the first time it appears."
        ),
        "sentence_style": "Clear sentences of 10-15 words. Simple compound sentences (\"and\", \"but\", \"so\") are fine; avoid nested clauses.",
        "narrative_length": "4-6 sentences",
        "quiz_cognitive_level": (
            "Remember + Understand - recall facts and explain concepts in the "
            "student's own words. Light application (\"Which of these is an example of...\") is fine."
        ),
        "image_style": "children's book illustration style, moderate detail, Disney/Pixar-quality educational cartoon, clear focal character",
        "tts_speed": 0.95,
        "min_avg_sentence_words": 7.0,
        "max_avg_sentence_words": 18.0,
        "forbid_bare_recall": False,
    },
    "middle": {  # Grade 6, 7, 8 (ages ~10-13)
        "vocabulary": (
            "Use grade-level academic vocabulary. Subject-specific technical terms "
            "are fine as long as each is given a brief in-context definition on first use."
        ),
        "sentence_style": "Varied sentence structure up to ~20 words; compound and complex sentences allowed.",
        "narrative_length": "5-7 sentences, may include simple cause-and-effect reasoning",
        "quiz_cognitive_level": (
            "Understand + Apply + light Analyze - require applying a concept to a "
            "new example, or comparing/contrasting two ideas, not just recalling a fact."
        ),
        "image_style": "detailed educational illustration with richer background context and composition, still clearly age-appropriate and non-graphic",
        "tts_speed": 1.0,
        "min_avg_sentence_words": 10.0,
        "max_avg_sentence_words": 23.0,
        "forbid_bare_recall": True,
    },
    "upper": {  # Grade 9, 10 (ages ~13-15)
        "vocabulary": (
            "Use the full academic/subject vocabulary appropriate to the source "
            "document with minimal simplification - these students can handle "
            "genuine subject terminology."
        ),
        "sentence_style": "Natural complex sentences up to ~25 words, varied structure, closer to how the concept would be described in a textbook.",
        "narrative_length": "5-8 sentences, may include nuance, multiple perspectives, or a deeper cause-and-effect chain",
        "quiz_cognitive_level": (
            "Apply + Analyze + Evaluate - require comparison, justification, or "
            "critical evaluation between plausible options, not just recall or a single-step lookup."
        ),
        "image_style": "detailed, editorial/textbook-style educational illustration with mature composition, while remaining fully family-friendly and non-graphic",
        "tts_speed": 1.05,
        "min_avg_sentence_words": 13.0,
        "max_avg_sentence_words": 30.0,
        "forbid_bare_recall": True,
    },
}


class GradeBand(TypedDict):
    label: str
    descriptor: str
    tier: str


# Order matters only for iteration/display; the dict key is the canonical id
# sent from the frontend and stored in `stories.grade_level`.
GRADE_BANDS: dict[str, GradeBand] = {
    "KG1": {"label": "KG-1", "descriptor": "Kindergarten 1 students (ages 3-4)", "tier": "early"},
    "KG2": {"label": "KG-2", "descriptor": "Kindergarten 2 students (ages 4-5)", "tier": "early"},
    "1": {"label": "Grade 1", "descriptor": "Grade 1 students (ages 5-6)", "tier": "early"},
    "2": {"label": "Grade 2", "descriptor": "Grade 2 students (ages 6-7)", "tier": "early"},
    "3": {"label": "Grade 3", "descriptor": "Grade 3 students (ages 7-8)", "tier": "lower"},
    "4": {"label": "Grade 4", "descriptor": "Grade 4 students (ages 8-9)", "tier": "lower"},
    "5": {"label": "Grade 5", "descriptor": "Grade 5 students (ages 9-10)", "tier": "lower"},
    "6": {"label": "Grade 6", "descriptor": "Grade 6 students (ages 10-11)", "tier": "middle"},
    "7": {"label": "Grade 7", "descriptor": "Grade 7 students (ages 11-12)", "tier": "middle"},
    "8": {"label": "Grade 8", "descriptor": "Grade 8 students (ages 12-13)", "tier": "middle"},
    "9": {"label": "Grade 9", "descriptor": "Grade 9 students (ages 13-14)", "tier": "upper"},
    "10": {"label": "Grade 10", "descriptor": "Grade 10 students (ages 14-15)", "tier": "upper"},
}

_DEFAULT_TIER = "lower"
_DEFAULT_DESCRIPTOR = "Grade 4 students (ages 8-9)"


class ResolvedGradeSpec(TypedDict):
    label: str
    descriptor: str
    tier: str
    vocabulary: str
    sentence_style: str
    narrative_length: str
    quiz_cognitive_level: str
    image_style: str
    tts_speed: float
    min_avg_sentence_words: float
    max_avg_sentence_words: float
    forbid_bare_recall: bool


def resolve_grade_spec(grade_level: "str | int | None") -> ResolvedGradeSpec:
    """Look up the full content spec for a grade_level value from the client.

    Falls back to a sane grade-4 default for legacy/malformed values (e.g. an
    old client still sending a bare int, or a story generated before this
    table existed) instead of raising - a slightly-off default beats a hard
    failure on an otherwise-working upload.
    """
    key = str(grade_level).strip() if grade_level is not None else ""
    band = GRADE_BANDS.get(key)
    if band is None:
        band = {"label": _DEFAULT_DESCRIPTOR, "descriptor": _DEFAULT_DESCRIPTOR, "tier": _DEFAULT_TIER}
    tier_spec = TIER_SPECS[band["tier"]]
    return {**band, **tier_spec}  # type: ignore[typeddict-item]
