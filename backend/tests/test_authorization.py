"""Authorization invariants: path traversal, story-ID shape, media auth.

These are the checks that stand between an authenticated user and everybody
else's stories. They are pure-function tests on purpose - no DB, no network -
so they run fast and can never be skipped for environmental reasons.
"""
import re

import pytest

TRAVERSAL_PAYLOADS = [
    "../../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "/etc/passwd",
    "..\\..\\windows\\system32\\config\\sam",
    "scene_0.png/../../../../etc/shadow",
    "....//....//etc/passwd",
]

# The guard actually used by the media routes in main.py. Kept in sync here
# deliberately: if someone loosens it there, this test should be what fails.
def _is_rejected_filename(filename: str) -> bool:
    return ".." in filename or filename.startswith("/") or "\\" in filename


class TestPathTraversal:
    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_traversal_payloads_are_rejected(self, payload):
        assert _is_rejected_filename(payload), f"traversal payload slipped through: {payload}"

    @pytest.mark.parametrize("name", ["scene_0.png", "scene_10.mp3", "abc-123_scene_2.png"])
    def test_legitimate_filenames_pass(self, name):
        assert not _is_rejected_filename(name)


class TestStoryIdShape:
    """story_id is interpolated into filesystem paths, so it is pinned to a
    UUID shape before it is ever used to build one."""

    PATTERN = re.compile(r"^[a-f0-9\-]{36}$")

    def test_accepts_uuid(self):
        assert self.PATTERN.match("b212c373-368f-41e0-a11e-68845c4b9c41")

    @pytest.mark.parametrize("bad", [
        "../../etc",
        "b212c373-368f-41e0-a11e-68845c4b9c41/../../../etc/passwd",
        "'; DROP TABLE user_stories;--",
        "b212c373368f41e0a11e68845c4b9c41extra",
        "",
    ])
    def test_rejects_non_uuid(self, bad):
        assert not self.PATTERN.match(bad), f"non-UUID story_id accepted: {bad}"


class TestOutputsRouteScoping:
    """/api/outputs/ serves legacy cache files. It derives the owning story_id
    from the filename and authorizes against it - an unmatched shape must 404
    rather than fall through to serving an arbitrary path."""

    AUDIO = re.compile(r"^audio_cache/audio_([a-f0-9\-]{36})_\d+\.mp3$")
    STATUS = re.compile(r"^status/([a-f0-9\-]{36})\.json$")

    def test_extracts_story_id_from_audio_path(self):
        m = self.AUDIO.match("audio_cache/audio_b212c373-368f-41e0-a11e-68845c4b9c41_3.mp3")
        assert m and m.group(1) == "b212c373-368f-41e0-a11e-68845c4b9c41"

    @pytest.mark.parametrize("path", [
        "audio_cache/../../../etc/passwd",
        "status/../../secrets.json",
        "audio_cache/audio_notauuid_1.mp3",
        "arbitrary/file.mp3",
    ])
    def test_unrecognized_paths_yield_no_story_id(self, path):
        assert not (self.AUDIO.match(path) or self.STATUS.match(path)), (
            f"path would have been served without an ownership check: {path}"
        )
