"""Turn an internal generation exception into something a user can act on.

Why this exists
---------------
Every generation failure reached the user as the same seven words: "AI Generation
failed." The information needed to do better already existed at both ends -
`mark_story_failed` writes an `error` column, and the client already renders
`job.error` when present - but `/api/status/{job_id}` never returned the field,
so the client's fallback string was the only thing anyone ever saw.

That is worse than unhelpful. The failure on 2026-08-03 was a quiz that came back
with 7 questions instead of 10; the story itself was complete and correct. The
user was told "AI Generation failed", had no way to know a different document (or
simply retrying) would help, and had no confirmation their credit came back.

Design rules
------------
- One code per CAUSE, not per raise site. Several exceptions map to AI_BUSY
  because the user's action is identical for all of them: wait, retry.
- The message names what happened AND what to do next. "Something went wrong" is
  the thing this module exists to delete.
- Never leak internals. Model names, API providers, token counts, stack traces
  and file paths stay in the logs; the operator needs them, the user does not.
- `can_retry` gates the automatic second attempt. Retrying a bank receipt to see
  if it is educational this time costs real money to reach the same answer, so
  verdict-style failures are marked False deliberately.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    can_retry: bool


# Ordered most-specific first: classify() returns the first match, so a narrow
# signature must never sit below a broad one that would swallow it.
_RULES = (
    (
        ("not_educational_material",),
        "NOT_EDUCATIONAL",
        "This doesn't look like teaching or learning material. Try a lesson, "
        "textbook chapter, study guide, or article instead.",
        False,
    ),
    (
        ("content_unsuitable",),
        "CONTENT_UNSUITABLE",
        "This document contains content we can't turn into a children's story. "
        "Please try a different document.",
        False,
    ),
    (
        ("too short", "thin content", "not enough text", "no text could be extracted"),
        "DOCUMENT_TOO_THIN",
        "We couldn't read enough text from this file. It may be a scanned image, "
        "password-protected, or mostly blank. Try a document with selectable text.",
        False,
    ),
    (
        ("daily vision", "vision limit", "vision budget"),
        "READING_LIMIT_REACHED",
        "Today's document-reading limit has been reached. Please try again "
        "tomorrow, or use a shorter document.",
        False,
    ),
    (
        ("quota exceeded", "rate limit", "429", "tokens per minute", "tpm", "request too large"),
        "AI_BUSY",
        "Our AI service is busy right now. Please wait a minute and try again.",
        True,
    ),
    (
        ("usable quiz", "quiz' has only", "quiz' must"),
        "QUIZ_TOO_SHORT",
        "We couldn't build a usable quiz from this document - it may be too short "
        "or mostly images. Try a longer document, or ask for fewer questions.",
        True,
    ),
    (
        ("no ai model available", "configure groq_api_key", "api key"),
        "SERVICE_UNAVAILABLE",
        "Story generation is temporarily unavailable. Our team has been notified.",
        False,
    ),
    (
        ("timeout", "timed out", "connection", "read operation"),
        "NETWORK_TIMEOUT",
        "The connection to our AI service timed out. Please try again.",
        True,
    ),
    (
        ("story generation failed", "missing required field", "validation", "scenes' must", "json"),
        "STORY_INCOMPLETE",
        "The AI returned an incomplete story. We tried again automatically and it "
        "still didn't come through. Please try once more, or use a different document.",
        True,
    ),
)

_UNKNOWN = Failure(
    code="UNKNOWN",
    message="Something went wrong while building your story. Please try again.",
    can_retry=True,
)


def classify(error: object) -> Failure:
    """Map an exception (or its string) to a user-facing Failure.

    Total by construction: an unrecognised error still yields a Failure, because
    the caller is on the failure path already and must not fail again there.
    """
    text = str(error or "").lower()
    for needles, code, message, can_retry in _RULES:
        if any(n in text for n in needles):
            return Failure(code=code, message=message, can_retry=can_retry)
    return _UNKNOWN


def user_message(error: object, credit_refunded: bool = True) -> str:
    """The full sentence shown in the client's error banner.

    The refund line is appended here rather than in the client so that a caller
    which could NOT refund (see the except-branch around refund_credit) is unable
    to promise one by accident.
    """
    failure = classify(error)
    if credit_refunded:
        return f"{failure.message} Your credit has been restored."
    return failure.message


def is_retryable(error: object) -> bool:
    return classify(error).can_retry


def describe(error: object, credit_refunded: bool = True) -> dict:
    """Structured form for the status endpoint."""
    failure = classify(error)
    return {
        "error_code": failure.code,
        "error": user_message(error, credit_refunded),
        "credit_refunded": credit_refunded,
        "can_retry": failure.can_retry,
    }
