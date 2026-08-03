"""Capacity estimate shown on the confirm screen, BEFORE a credit is spent.

User requirement (2026-08-03): "If a user asks for 20 questions on a small
document ... you will say that the document can only produce XYZ questions
rather than what you asked. Continue or abort?"

The estimate is a free heuristic over the text the confirm screen already
extracts for language detection - no extra model call, no extra latency, no
quota. It only decides whether to ASK; the model is still told to produce the
exact requested count, and a real shortfall is reported afterwards as a
quiz_notice on a delivered story.

The failure mode that matters most here is a FALSE WARNING: telling someone
their perfectly good chapter can only make 3 questions is worse than saying
nothing, so thin native text must yield "no opinion", not a low number.
"""
import pytest

from services.story_service import (
    MIN_VIABLE_QUIZ,
    QUIZ_SIZE_OPTIONS,
    estimate_question_capacity,
)


def _prose(sentences, words_per_sentence=12):
    """Realistic-shaped text: full sentences, not one long run of characters."""
    body = " ".join(f"word{i}" for i in range(words_per_sentence))
    return " ".join(f"{body} number {n}." for n in range(sentences))


class TestNoOpinionWhenItCannotJudge:
    """A scanned PDF extracts as near-empty here but is vision-read during
    generation, so the estimate must abstain rather than under-report."""

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_empty_input_returns_none(self, text):
        assert estimate_question_capacity(text) is None

    def test_a_scanned_pdf_stub_returns_none_not_a_low_number(self):
        # What pypdf typically yields for an image-only page: a header, a page
        # number, maybe a caption.
        assert estimate_question_capacity("Chapter 2\n14\nFig. 2.1 Apparatus") is None

    def test_just_under_the_threshold_abstains(self):
        assert estimate_question_capacity("a" * 799) is None


class TestRealDocuments:
    def test_a_full_chapter_supports_the_largest_offered_size(self):
        """The NCERT Class 10 chemistry chapter that started all of this
        extracts to 13614 chars and demonstrably carries 20 distinct questions -
        it generated 10 strong ones with room to spare."""
        assert estimate_question_capacity(_prose(150)) == max(QUIZ_SIZE_OPTIONS)

    def test_a_short_handout_supports_fewer_than_the_default(self):
        """~2000 chars: enough for a story, not enough for 10 distinct
        questions. This is the case the confirm dialog exists for."""
        estimate = estimate_question_capacity(_prose(22))
        assert MIN_VIABLE_QUIZ <= estimate < 10

    def test_the_estimate_never_exceeds_the_largest_offered_size(self):
        """Offering "about 137 questions" on a whole textbook would be absurd
        and unactionable - the user can only pick from QUIZ_SIZE_OPTIONS."""
        assert estimate_question_capacity(_prose(4000)) == max(QUIZ_SIZE_OPTIONS)

    def test_it_never_reports_below_the_viable_floor(self):
        """A capacity of 0 or 1 is a generation-quality problem, not a
        quiz-size one - reporting it here would push the user into a dialog
        that cannot help them."""
        for n in range(6, 30):
            estimate = estimate_question_capacity(_prose(n))
            if estimate is not None:
                assert estimate >= MIN_VIABLE_QUIZ


class TestLengthAloneIsNotEnough:
    def test_padding_does_not_buy_capacity(self):
        """A document can clear the character budget while carrying almost no
        separate assertions. Character count alone would rate this as a full
        20-question document; the sentence bound has to catch it."""
        padded = "The mitochondrion is the powerhouse of the cell. " + ("x" * 12000)
        assert estimate_question_capacity(padded) == MIN_VIABLE_QUIZ

    def test_fragments_do_not_count_as_questionable_material(self):
        """Slide bullets and figure labels are not question material - each
        needs enough words to carry an assertion."""
        bullets = "\n".join(["Photosynthesis."] * 400)
        assert estimate_question_capacity(bullets) == MIN_VIABLE_QUIZ


class TestMonotonic:
    def test_more_material_never_estimates_lower(self):
        """A user who adds pages must never be told the document got thinner."""
        previous = 0
        for n in (10, 20, 40, 80, 160):
            estimate = estimate_question_capacity(_prose(n)) or 0
            assert estimate >= previous
            previous = estimate
