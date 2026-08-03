"""Gemini primary, Groq fallback - and the document budgets that differ between them.

Context (2026-08-03): story generation ran on Groq's free on_demand tier, capped
at 8000 tokens/minute with prompt + requested max_tokens charged against the same
budget. That forced the document to be truncated to 6500 characters before the
model saw it. Measured on a real NCERT Class 10 chemistry chapter (13614 chars),
the cut silently discarded washing soda, bleaching powder, the chlor-alkali
process and Plaster of Paris - most of the second half of the chapter.

Gemini takes ~1M input tokens, so the truncation is gone on the primary path. The
Groq path remains as a fallback for Gemini 503s ("experiencing high demand",
observed in practice), and it still has to truncate - so the two budgets must
stay separate.
"""
import pytest

from services.story_service import (
    _GROQ_MAX_DOC_CHARS,
    _STORY_MAX_DOC_CHARS,
    _STORY_MODEL,
    StoryService,
)


class _FakeGeminiResponse:
    def __init__(self, text):
        self.text = text


class _FakeGemini:
    """Records what it was asked, so the test can assert on the prompt sent."""

    def __init__(self, fail=False, text='{"ok": true}'):
        self.fail = fail
        self.text = text
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents})
        if self.fail:
            raise Exception("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return _FakeGeminiResponse(self.text)


class _FakeGroqOK:
    """Mimics groq_client.chat.completions.create by being all three objects."""

    def __init__(self, text='{"from": "groq"}'):
        self.text = text
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, model, messages, temperature, max_tokens, response_format):
        self.calls.append({"model": model, "messages": messages, "max_tokens": max_tokens})

        class _M:
            content = self.text

        class _C:
            message = _M()

        class _R:
            choices = [_C()]

        return _R()


@pytest.fixture
def svc():
    s = StoryService()
    s._gemini_client = None
    s.groq_client = None
    return s


class TestProviderSelection:
    def test_gemini_is_used_when_available(self, svc):
        gem = _FakeGemini()
        svc._gemini_client = gem
        svc.groq_client = _FakeGroqOK()

        out = svc._call_story_model("INSTRUCTIONS", "document text")
        assert out == '{"ok": true}'
        assert gem.calls, "Gemini must be the primary path"
        assert gem.calls[0]["model"] == _STORY_MODEL
        assert not svc.groq_client.calls, "Groq must not be called when Gemini succeeds"

    def test_groq_takes_over_when_gemini_is_overloaded(self, svc):
        """The 503 is real and comes from Google's capacity, not from us. Failing
        the user's upload over someone else's traffic spike is not acceptable."""
        svc._gemini_client = _FakeGemini(fail=True)
        svc.groq_client = _FakeGroqOK()

        out = svc._call_story_model("INSTRUCTIONS", "document text")
        assert out == '{"from": "groq"}'
        assert svc.groq_client.calls, "Groq fallback never fired"

    def test_groq_alone_still_works(self, svc):
        """Gemini key absent - the app must still generate, not silently do nothing."""
        svc.groq_client = _FakeGroqOK()
        assert svc._call_story_model("INSTRUCTIONS", "doc") == '{"from": "groq"}'

    def test_no_provider_configured_returns_none(self, svc):
        assert svc._call_story_model("INSTRUCTIONS", "doc") is None

    def test_gemini_failure_with_no_groq_raises_rather_than_silently_returning_none(self, svc):
        """A None here would be read upstream as 'no content extracted' and
        reported to the user as a document problem, which it is not."""
        svc._gemini_client = _FakeGemini(fail=True)
        with pytest.raises(Exception, match="Story generation failed"):
            svc._call_story_model("INSTRUCTIONS", "doc")


class TestDocumentBudgets:
    def test_gemini_budget_is_far_larger_than_groq(self):
        assert _STORY_MAX_DOC_CHARS > _GROQ_MAX_DOC_CHARS * 10

    def test_gemini_receives_the_whole_chapter(self, svc):
        """The exact regression: a 13614-char chapter must reach the model intact."""
        gem = _FakeGemini()
        svc._gemini_client = gem
        doc = "x" * 13614 + "CHLOR-ALKALI"

        svc._call_story_model("INSTRUCTIONS", doc)
        sent = gem.calls[0]["contents"]
        assert "CHLOR-ALKALI" in sent, "content past 6500 chars was dropped again"
        assert "[Document truncated for length]" not in sent

    def test_gemini_still_truncates_an_absurd_upload(self, svc):
        gem = _FakeGemini()
        svc._gemini_client = gem

        svc._call_story_model("INSTRUCTIONS", "y" * (_STORY_MAX_DOC_CHARS + 5000))
        assert "[Document truncated for length]" in gem.calls[0]["contents"]

    def test_groq_fallback_truncates_to_its_own_smaller_budget(self, svc):
        """Groq's 8000 TPM ceiling has not moved just because it is now the
        fallback - sending it the full document would 413."""
        svc._gemini_client = _FakeGemini(fail=True)
        svc.groq_client = _FakeGroqOK()

        svc._call_story_model("INSTRUCTIONS", "z" * 50000)
        sent = svc.groq_client.calls[0]["messages"][1]["content"]
        assert "[Document truncated for length]" in sent
        assert sent.count("z") <= _GROQ_MAX_DOC_CHARS


class TestModelsAreDistinct:
    def test_vision_and_story_use_different_models(self):
        """Gemini meters RPM/RPD per model. Sharing one model between page-reading
        and story-writing would put both jobs in the same 500-requests-a-day pool
        instead of giving each its own."""
        assert StoryService._VISION_MODEL != _STORY_MODEL
