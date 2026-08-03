"""Vision extraction: page cap, ordering, truncation disclosure, loop safety.

Regression cover for the 2026-08-03 availability finding: extraction rendered
every page of a document into memory before making any API call, then issued
one vision call per page. A 400-page PDF weighs 169KB - far under the 20MB
upload cap - so a single upload could OOM the process and exhaust the shared
daily API quota for every user.
"""
import asyncio

import pytest

from services.concurrency import VISION_MAX_PAGES


def _pdf_with_pages(n: int) -> bytes:
    import fitz

    doc = fitz.open()
    for i in range(n):
        doc.new_page().insert_text((72, 100), f"Page {i + 1} content")
    data = doc.tobytes()
    doc.close()
    return data


class TestPageCap:
    def test_caps_vision_calls_on_huge_document(self, story_service):
        calls = []
        story_service._vision_read_image = lambda b, mime="image/png": calls.append(1) or "text"

        story_service._vision_extract_pdf_pages(_pdf_with_pages(VISION_MAX_PAGES * 5))

        assert len(calls) == VISION_MAX_PAGES, (
            f"expected the cap to hold at {VISION_MAX_PAGES}; an uncapped run would "
            f"have made {VISION_MAX_PAGES * 5} calls"
        )

    def test_small_document_is_not_capped(self, story_service):
        calls = []
        story_service._vision_read_image = lambda b, mime="image/png": calls.append(1) or "text"

        story_service._vision_extract_pdf_pages(_pdf_with_pages(3))

        assert len(calls) == 3

    def test_truncation_is_disclosed_to_the_model(self, story_service):
        """A partial read presented as a whole read is how the fabrication bug
        started. If pages are dropped, the text must say so."""
        out = story_service._vision_extract_pdf_pages(_pdf_with_pages(VISION_MAX_PAGES + 5))
        assert "[Only the first" in out
        assert str(VISION_MAX_PAGES) in out

    def test_no_spurious_truncation_notice_when_under_cap(self, story_service):
        out = story_service._vision_extract_pdf_pages(_pdf_with_pages(2))
        assert "[Only the first" not in out


class TestOrdering:
    def test_pages_stay_in_order_despite_concurrency(self, story_service):
        """Concurrency must change how fast pages are read, never what order
        they appear in - the story is generated in document order."""
        seen = {"n": 0}

        def fake_read(b, mime="image/png"):
            seen["n"] += 1
            return f"marker{seen['n']}"

        story_service._vision_read_image = fake_read
        out = story_service._vision_extract_pdf_pages(_pdf_with_pages(5))

        positions = [out.index(f"--- Page {i} ") for i in range(1, 6)]
        assert positions == sorted(positions), "page markers are out of document order"


class TestEventLoopSafety:
    """The concurrent batch is driven by asyncio.run from synchronous code.
    asyncio.run raises RuntimeError if a loop is already running on the thread,
    which would turn a future refactor into a hard extraction failure."""

    def test_works_from_sync_context(self, story_service):
        assert story_service._vision_read_images_blocking([b"a", b"b"]) == [
            "stubbed page text",
            "stubbed page text",
        ]

    def test_works_inside_a_running_loop(self, story_service):
        async def inside():
            return story_service._vision_read_images_blocking([b"a", b"b"])

        assert asyncio.run(inside()) == ["stubbed page text", "stubbed page text"]


class TestFailureIsolation:
    def test_one_failing_page_does_not_kill_the_document(self, story_service):
        """A transient API failure on page 2 must not discard pages 1 and 3."""
        state = {"n": 0}

        def flaky(b, mime="image/png"):
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("simulated vision API failure")
            return "good page"

        story_service._vision_read_image = flaky
        out = story_service._vision_extract_pdf_pages(_pdf_with_pages(3))
        assert out.count("good page") == 2
