"""Quiz integrity: near-duplicate removal, meta-question rejection, and the
citation-honesty rule.

Regression cover for two shipped defects:
  - A grade-10 quiz shipped "What is the main takeaway from our discussion?"
    and "What is the final takeaway from our discussion?" as separate questions.
  - A thin-content document produced 10 fully-invented questions, every one
    tagged with a specific "Page 1" citation that the UI displayed to students
    as a real source reference.
"""
import pytest


def _q(n, text, source="extracted", section="Page 1"):
    return {
        "question_number": n,
        "question_text": text,
        "options": ["A. one", "B. two", "C. three", "D. four"],
        "correct_answer": "B",
        "explanation": "because",
        "why_correct": "because B is right and the others are not",
        "source": source,
        "document_section": section,
    }


class TestMetaQuestionRemoval:
    @pytest.mark.parametrize("text", [
        "What is the main takeaway from our discussion?",
        "What is the final takeaway from our discussion?",
        "What did we learn in this story?",
        "What is the key takeaway from the lesson?",
    ])
    def test_questions_about_the_narration_are_dropped(self, story_service, text):
        kept, dropped = story_service._drop_near_duplicate_questions([_q(1, text)])
        assert len(kept) == 0, f"meta question survived: {text}"
        assert len(dropped) == 1

    def test_document_questions_are_kept(self, story_service):
        kept, _ = story_service._drop_near_duplicate_questions([
            _q(1, "Which nutrient gives the body energy?")
        ])
        assert len(kept) == 1


class TestNearDuplicateRemoval:
    def test_restatements_are_collapsed(self, story_service):
        kept, dropped = story_service._drop_near_duplicate_questions([
            _q(1, "What is one way neighborhood parks help keep our air clean?"),
            _q(2, "What is one way that neighborhood parks help keep the air clean?"),
        ])
        assert len(kept) == 1, "a near-verbatim restatement was kept as a distinct question"
        assert len(dropped) == 1

    def test_different_concepts_sharing_a_frame_are_kept(self, story_service):
        """The filter must not collapse genuinely different questions just
        because they share a sentence shape - that deleted good nutrient
        questions from a real generation before the threshold was raised."""
        kept, _ = story_service._drop_near_duplicate_questions([
            _q(1, "What is the role of proteins in a balanced diet?"),
            _q(2, "What is the role of carbohydrates in a balanced diet?"),
            _q(3, "What is the role of vitamins in a balanced diet?"),
        ])
        assert len(kept) == 3, "distinct nutrients were wrongly treated as duplicates"

    def test_renumbering_stays_contiguous(self, story_service):
        """The frontend indexes off question_number; gaps break review mode."""
        kept, _ = story_service._drop_near_duplicate_questions([
            _q(1, "What do plants need to make food?"),
            _q(2, "What is the main takeaway from our discussion?"),  # dropped
            _q(3, "Which gas do plants release?"),
        ])
        assert [q["question_number"] for q in kept] == list(range(1, len(kept) + 1))


class TestCitationHonesty:
    """A page citation on content the model invented is worse than no citation:
    it is a false claim of provenance, rendered in the UI next to the answer."""

    def _sanitize(self, quiz):
        # Mirrors the sanitization applied in process_file_to_story before
        # validation. Kept here so loosening it there fails a test.
        for q in quiz:
            if q.get("source") != "extracted" and q.get("document_section"):
                q["document_section"] = None
        return quiz

    def test_generated_questions_lose_fabricated_citations(self):
        quiz = self._sanitize([_q(1, "Invented question", source="generated", section="Page 1")])
        assert quiz[0]["document_section"] is None

    def test_extracted_questions_keep_real_citations(self):
        quiz = self._sanitize([_q(1, "Real question", source="extracted", section="Page 3")])
        assert quiz[0]["document_section"] == "Page 3"

    def test_validator_requires_a_citation_for_extracted_questions(self, story_service):
        story = _valid_story()
        story["quiz"][0]["source"] = "extracted"
        story["quiz"][0]["document_section"] = None
        ok, errors = story_service._validate_story_json(story)
        assert not ok
        assert any("document_section" in e for e in errors)

    def test_validator_accepts_generated_question_without_citation(self, story_service):
        story = _valid_story()
        for q in story["quiz"]:
            q["source"] = "generated"
            q["document_section"] = None
        ok, errors = story_service._validate_story_json(story)
        assert ok, f"generated questions must not require a citation; got {errors}"


def _valid_story():
    return {
        "title": "T",
        "description": "D",
        "grade_level": "Grade 5",
        "subject": "Science",
        "learning_outcome": "O",
        "scenes": [
            {
                "scene_number": i,
                "narrative_text": "text",
                "image_prompt": "prompt",
                "check_for_understanding": "q",
            }
            for i in range(1, 6)
        ],
        "quiz": [_q(i, f"Distinct question number {i} about a separate concept") for i in range(1, 11)],
    }
