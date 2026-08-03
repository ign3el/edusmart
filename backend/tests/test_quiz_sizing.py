"""Quiz length is a user-chosen target, never a reason to destroy a story.

Regression cover for 2026-08-03, where all three of these compounded to fail a
complete grade-10 generation:
  1. the 10-question minimum was a FATAL validation error
  2. the top-up call self-429'd (it re-sent the full document seconds after the
     main call had reserved the entire per-minute token budget)
  3. _ensure_minimum_questions could RETURN FEWER questions than it was given
     (reproduced live: 7 in, 4 out) because it de-duplicated across
     existing + new and was free to drop the existing ones
"""
import pytest

from services.story_service import (
    DEFAULT_QUIZ_SIZE,
    MIN_VIABLE_QUIZ,
    QUIZ_SIZE_OPTIONS,
    normalize_quiz_size,
)


def _q(n, text=None):
    return {
        "question_number": n,
        "question_text": text or f"Distinct question {n} about a separate concept?",
        "options": ["A. one", "B. two", "C. three", "D. four"],
        "correct_answer": "B",
        "explanation": "because",
        "why_correct": "detailed reasoning",
        # source 'extracted' makes document_section conditionally required - see
        # the citation-sanitisation rule in _validate_story_json.
        "source": "extracted",
        "document_section": f"Section {n}",
    }


def _story(quiz, scenes=5):
    return {
        "title": "T",
        "description": "D",
        "grade_level": "10",
        "subject": "Science",
        "learning_outcome": "L",
        "scenes": [
            {
                "scene_number": i + 1,
                "narrative_text": "Some narrative text for this scene.",
                "image_prompt": "a prompt",
                "check_for_understanding": "a question",
            }
            for i in range(scenes)
        ],
        "quiz": quiz,
    }


class TestNormalizeQuizSize:
    @pytest.mark.parametrize("n", QUIZ_SIZE_OPTIONS)
    def test_offered_sizes_pass_through(self, n):
        assert normalize_quiz_size(n) == n

    def test_string_form_is_accepted(self):
        """It arrives as a multipart form field, so it is a string on the wire."""
        assert normalize_quiz_size("15") == 15

    @pytest.mark.parametrize(
        "junk", [None, "", "abc", {}, [], "NaN", "10; DROP TABLE stories"]
    )
    def test_junk_falls_back_to_the_default_instead_of_raising(self, junk):
        """A malformed form field must not fail an upload whose file already
        made it across the wire."""
        assert normalize_quiz_size(junk) == DEFAULT_QUIZ_SIZE

    def test_out_of_range_snaps_to_nearest_offered_size(self):
        assert normalize_quiz_size(12) == 10
        assert normalize_quiz_size(14) == 15
        assert normalize_quiz_size(1000) == 20
        assert normalize_quiz_size(-5) == 5


class TestShortQuizIsNotFatal:
    def test_seven_questions_is_accepted(self, story_service):
        """The exact case that failed in production: a complete story whose only
        defect was 7 quiz questions instead of 10."""
        ok, errors = story_service._validate_story_json(_story([_q(i + 1) for i in range(7)]))
        assert ok, errors

    @pytest.mark.parametrize("n", [MIN_VIABLE_QUIZ, MIN_VIABLE_QUIZ + 1, 7, 9])
    def test_anything_at_or_above_the_floor_is_accepted(self, story_service, n):
        ok, errors = story_service._validate_story_json(_story([_q(i + 1) for i in range(n)]))
        assert ok, errors

    def test_a_near_empty_quiz_is_still_fatal(self, story_service):
        """Not fatal is not the same as never fatal - one question means the
        generation itself went wrong, not that the document was thin."""
        ok, errors = story_service._validate_story_json(_story([_q(1)]))
        assert not ok
        assert any("usable quiz" in e for e in errors)

    def test_structural_defects_are_still_caught_in_a_short_quiz(self, story_service):
        """The old code only validated question FIELDS when the count was >= 10,
        so a short quiz skipped field checks entirely."""
        broken = _q(1)
        del broken["correct_answer"]
        ok, errors = story_service._validate_story_json(_story([broken] + [_q(i + 2) for i in range(6)]))
        assert not ok
        assert any("correct_answer" in e for e in errors)


class TestTopUpNeverShrinks:
    def test_returns_at_least_what_it_was_given(self, story_service, monkeypatch):
        """The reproduced bug: 7 questions in, 4 out.

        The model is made to return duplicates of the EXISTING questions, which
        is what made the old de-duplication delete them.
        """
        existing = [_q(i + 1, "What is the pH of a neutral solution?") for i in range(7)]

        class _Resp:
            class _C:
                class _M:
                    content = (
                        '{"questions": ['
                        '{"question_text": "What is the pH of a neutral solution?"},'
                        '{"question_text": "What is the pH of a neutral solution?"}'
                        "]}"
                    )
                message = _M()
            choices = [_C()]

        monkeypatch.setattr(story_service, "_call_with_exponential_backoff", lambda fn: _Resp())

        out = story_service._ensure_minimum_questions(
            {"quiz": list(existing)}, "some document text", "10", target=10
        )
        assert len(out["quiz"]) >= len(existing), "a top-up must never shrink the quiz"

    def test_a_failed_top_up_call_leaves_the_quiz_untouched(self, story_service, monkeypatch):
        existing = [_q(i + 1) for i in range(7)]
        monkeypatch.setattr(
            story_service,
            "_call_with_exponential_backoff",
            lambda fn: (_ for _ in ()).throw(Exception("429 rate limit")),
        )
        out = story_service._ensure_minimum_questions(
            {"quiz": list(existing)}, "text", "10", target=10
        )
        assert len(out["quiz"]) == 7

    def test_it_does_not_overshoot_the_target(self, story_service, monkeypatch):
        """The model is asked for an exact count but regularly returns more, and
        the extras are the weakest ones."""
        existing = [_q(i + 1) for i in range(7)]

        # Wording must be genuinely varied, not "question 1/2/3" with a changing
        # digit - the duplicate filter compares token overlap, so near-identical
        # phrasings would (correctly) be rejected and this would stop testing
        # overshoot at all.
        candidates = [
            "Which gas forms when zinc reacts with dilute sulphuric acid?",
            "Why does copper fail to displace hydrogen from an acid?",
            "How would you separate sodium chloride from sand?",
            "What happens to litmus paper dipped in aqueous ammonia?",
            "Predict the pH after mixing equal strong acid and strong base.",
            "Compare the conductivity of molten and solid ionic compounds.",
            "Explain why concentrated sulphuric acid is stored in glass.",
            "Evaluate whether neutralisation is always exothermic.",
        ]

        class _Resp:
            class _C:
                class _M:
                    content = (
                        '{"questions": ['
                        + ",".join(f'{{"question_text": "{c}"}}' for c in candidates)
                        + "]}"
                    )
                message = _M()
            choices = [_C()]

        monkeypatch.setattr(story_service, "_call_with_exponential_backoff", lambda fn: _Resp())
        out = story_service._ensure_minimum_questions(
            {"quiz": list(existing)}, "text", "10", target=10
        )
        assert len(out["quiz"]) == 10

    def test_already_at_target_makes_no_api_call(self, story_service, monkeypatch):
        def _boom(fn):
            raise AssertionError("must not call the model when the target is met")

        monkeypatch.setattr(story_service, "_call_with_exponential_backoff", _boom)
        quiz = [_q(i + 1) for i in range(10)]
        out = story_service._ensure_minimum_questions({"quiz": quiz}, "text", "10", target=10)
        assert len(out["quiz"]) == 10


class TestQuizSizeDoesNotDriveSceneCount:
    """User requirement, 2026-08-03: 'Asking for 10 questions doesn't mean 20
    scenes will be reduced.' The prompt must say so explicitly, because the model
    otherwise tends to align the two counts."""

    def test_prompt_states_the_two_are_independent(self):
        import inspect
        from services.story_service import StoryService

        src = inspect.getsource(StoryService.process_file_to_story)
        assert "INDEPENDENT of scene count" in src
        assert "{quiz_size}" in src, "quiz size must be interpolated, not hardcoded"

    def test_scene_minimum_is_unaffected_by_a_small_quiz(self, story_service):
        """A 5-question quiz must not license a 3-scene story."""
        ok, errors = story_service._validate_story_json(
            _story([_q(i + 1) for i in range(5)], scenes=3)
        )
        assert not ok
        assert any("scenes" in e for e in errors)
