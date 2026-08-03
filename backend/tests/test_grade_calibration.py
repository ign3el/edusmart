"""Grade calibration: is the output actually pitched at the grade requested?

Regression cover for the 2026-08-03 finding that grade_bands.py specified
vocabulary, sentence style and quiz cognitive level per tier, all of it was
delivered to the model, and NONE of it was ever checked - a grade-10
generation came back indistinguishable from a grade-5 one and shipped.

The checker is advisory by design (one retry, then ship): mean sentence length
is a proxy for reading level, not reading level itself, and refusing to return
a story the user already paid a credit for is worse than a mis-pitched one.
These tests pin the behaviour of the measurement, not of the retry.
"""
import pytest

from services.grade_bands import resolve_grade_spec

# The quiz that actually shipped for grade 10 on 2026-07-26.
REAL_GRADE10_QUIZ = [
    "What is the main purpose of a balanced diet?",
    "What are the major food groups?",
    "Why is it important to include a variety of foods from different food groups?",
    "What is the main difference between Meal Plan A and Meal Plan B?",
    "What is the main purpose of a balanced meal plan for a child?",
    "Why is it important to limit our intake of unhealthy foods and drinks?",
    "What is the main takeaway from our discussion?",
    "How can we apply the concepts we learned?",
    "What is the importance of critical thinking when it comes to food choices?",
    "What is the final takeaway from our discussion?",
]

UPPER_TIER_QUIZ = [
    "Why would replacing whole fruit with juice reduce the fibre benefit?",
    "Compare Meal Plan A and Meal Plan B in terms of micronutrient coverage.",
    "Which meal would best support sustained athletic performance, and why?",
    "What is the likely effect of a diet lacking vitamins over several months?",
    "Evaluate whether a high-protein breakfast alone constitutes a balanced meal.",
]

SIMPLE_PROSE = "A balanced diet is essential. It provides nutrients. They help the body."
UPPER_PROSE = (
    "Carbohydrates supply the glucose that muscle tissue oxidises during sustained "
    "exertion, which is why endurance athletes prioritise them before competition "
    "rather than relying on protein alone for energy."
)


def _story(prose, questions):
    return {
        "scenes": [{"narrative_text": prose}],
        "quiz": [{"question_text": q} for q in questions],
    }


class TestReadingLevel:
    def test_flags_grade5_prose_submitted_as_grade10(self, story_service):
        problems = story_service._check_grade_calibration(
            _story(SIMPLE_PROSE, UPPER_TIER_QUIZ), "10"
        )
        assert any("too simple" in p for p in problems)

    def test_accepts_upper_tier_prose_at_grade10(self, story_service):
        problems = story_service._check_grade_calibration(
            _story(UPPER_PROSE, UPPER_TIER_QUIZ), "10"
        )
        assert problems == [], f"a genuinely upper-tier story was flagged: {problems}"

    def test_accepts_simple_prose_at_grade5(self, story_service):
        """The same prose that is too simple for grade 10 must be fine for
        grade 5 - otherwise the check is just a length filter."""
        problems = story_service._check_grade_calibration(
            _story(
                "Sam opens a bright kitchen and says a balanced diet means eating many "
                "different foods. He shows a plate divided into coloured sections.",
                ["Which nutrient gives the body energy?"],
            ),
            "5",
        )
        assert problems == []

    def test_flags_textbook_prose_submitted_for_kindergarten(self, story_service):
        problems = story_service._check_grade_calibration(
            _story(UPPER_PROSE, ["What colour is the apple?"]), "KG1"
        )
        assert any("too complex" in p for p in problems)

    def test_empty_narrative_is_not_flagged(self, story_service):
        """No text is a structural problem for the validator to catch, not a
        calibration problem - don't produce a misleading second error."""
        assert story_service._check_grade_calibration(_story("", []), "10") == []


class TestQuizCognitiveLevel:
    def test_flags_the_real_shipped_recall_heavy_quiz(self, story_service):
        problems = story_service._check_grade_calibration(
            _story(UPPER_PROSE, REAL_GRADE10_QUIZ), "10"
        )
        assert any("bare-recall" in p for p in problems), (
            "the exact quiz that shipped mis-calibrated was not detected"
        )

    def test_accepts_reasoning_heavy_quiz(self, story_service):
        problems = story_service._check_grade_calibration(
            _story(UPPER_PROSE, UPPER_TIER_QUIZ), "10"
        )
        assert not any("bare-recall" in p for p in problems)

    def test_recall_is_allowed_at_lower_tiers(self, story_service):
        """Grade 5's spec explicitly asks for Remember + Understand. Recall
        questions there are correct, not a defect."""
        problems = story_service._check_grade_calibration(
            _story("Sam eats an apple. It gives him vitamins to stay healthy.", REAL_GRADE10_QUIZ),
            "5",
        )
        assert not any("bare-recall" in p for p in problems)


class TestBandsAreCoherent:
    @pytest.mark.parametrize("grade", ["KG1", "2", "5", "8", "10"])
    def test_every_tier_has_a_usable_band(self, grade):
        spec = resolve_grade_spec(grade)
        assert spec["min_avg_sentence_words"] < spec["max_avg_sentence_words"]

    def test_bands_increase_with_tier(self):
        order = ["KG1", "3", "6", "9"]
        mins = [resolve_grade_spec(g)["min_avg_sentence_words"] for g in order]
        assert mins == sorted(mins), "reading-level floors must rise with grade"

    def test_upper_tiers_forbid_bare_recall_lower_tiers_do_not(self):
        assert resolve_grade_spec("KG1")["forbid_bare_recall"] is False
        assert resolve_grade_spec("5")["forbid_bare_recall"] is False
        assert resolve_grade_spec("8")["forbid_bare_recall"] is True
        assert resolve_grade_spec("10")["forbid_bare_recall"] is True
