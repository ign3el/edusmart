"""Every failure cause must reach the user as its own actionable message.

Regression cover for the 2026-08-03 report: a story whose only defect was a
7-question quiz (instead of 10) was destroyed, the credit refunded, and the user
told nothing but "AI Generation failed." The information existed at both ends -
mark_story_failed wrote an `error` column and the client already rendered
`job.error` - but /api/status never returned the field, so the client's generic
fallback was the only message anyone ever saw.
"""
import pytest

from services import failure_reasons as fr


class TestClassification:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("not_educational_material: this is a bank receipt", "NOT_EDUCATIONAL"),
            ("content_unsuitable: graphic violence", "CONTENT_UNSUITABLE"),
            ("Document too short to build a story", "DOCUMENT_TOO_THIN"),
            ("daily vision limit reached (400/400 pages today)", "READING_LIMIT_REACHED"),
            ("Error code: 429 - rate limit reached for model", "AI_BUSY"),
            ("AI Service quota exceeded. Please try again later", "AI_BUSY"),
            ("Request too large... TPM: Limit 8000, Requested 10832", "AI_BUSY"),
            ("'quiz' has only 1 question(s); at least 3 are needed", "QUIZ_TOO_SHORT"),
            ("No AI model available for story generation. Configure GROQ_API_KEY.", "SERVICE_UNAVAILABLE"),
            ("The read operation timed out", "NETWORK_TIMEOUT"),
            ("Story generation failed: ['Missing required field: title']", "STORY_INCOMPLETE"),
        ],
    )
    def test_each_cause_gets_its_own_code(self, raw, expected):
        assert fr.classify(raw).code == expected

    def test_unrecognised_error_still_returns_a_failure(self):
        """The classifier runs on the failure path. It must never raise there."""
        assert fr.classify(RuntimeError("something entirely new")).code == "UNKNOWN"

    def test_none_is_tolerated(self):
        assert fr.classify(None).code == "UNKNOWN"

    def test_accepts_an_exception_object_not_just_a_string(self):
        assert fr.classify(Exception("content_unsuitable: nope")).code == "CONTENT_UNSUITABLE"


class TestMessages:
    def test_no_message_is_the_old_generic_string(self):
        """The whole point of this module."""
        for _, code, message, _ in fr._RULES:
            assert "AI Generation failed" not in message, code

    def test_every_message_says_what_to_do_next(self):
        """A cause with no next step is just a nicer-sounding dead end."""
        actionable = ("try", "please", "notified", "tomorrow", "instead", "wait")
        for _, code, message, _ in fr._RULES:
            assert any(w in message.lower() for w in actionable), f"{code}: {message}"

    def test_internals_never_leak_to_the_user(self):
        """Model names, providers and token counts belong in the logs only."""
        leaky = ("groq", "gemini", "runpod", "gpt-oss", "tpm", "429", "traceback", "json")
        for _, code, message, _ in fr._RULES:
            low = message.lower()
            for term in leaky:
                assert term not in low, f"{code} leaks {term!r}: {message}"

    def test_refund_is_stated_when_it_happened(self):
        msg = fr.user_message("content_unsuitable: nope", credit_refunded=True)
        assert "credit has been restored" in msg

    def test_refund_is_not_promised_when_it_failed(self):
        """The refund can fail independently. Promising one that did not happen
        is worse than staying quiet about it."""
        msg = fr.user_message("content_unsuitable: nope", credit_refunded=False)
        assert "restored" not in msg


class TestRetryPolicy:
    @pytest.mark.parametrize(
        "raw",
        [
            "Story generation failed: ['Missing required field: title']",
            "Error code: 429 - rate limit reached",
            "The read operation timed out",
        ],
    )
    def test_dice_roll_failures_are_retried(self, raw):
        assert fr.is_retryable(raw) is True

    @pytest.mark.parametrize(
        "raw",
        [
            "not_educational_material: bank statement",
            "content_unsuitable: graphic content",
            "Document too short to build a story",
            "daily vision limit reached",
        ],
    )
    def test_verdicts_are_not_retried(self, raw):
        """Re-asking whether a receipt is educational costs a full generation to
        arrive at the identical answer."""
        assert fr.is_retryable(raw) is False


class TestDescribe:
    def test_shape_matches_what_the_status_endpoint_returns(self):
        d = fr.describe("content_unsuitable: nope", credit_refunded=True)
        assert set(d) == {"error_code", "error", "credit_refunded", "can_retry"}
        assert d["error_code"] == "CONTENT_UNSUITABLE"
        assert d["credit_refunded"] is True
