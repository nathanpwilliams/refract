"""Unit test for the SCORM packager's pure rendering layer (no database).

Builds a package from a synthetic CourseData and asserts the zip is
well-formed and self-contained.
"""
import io
import zipfile

from app.services import scorm
from app.services.scorm import Block, CourseData, Lesson, Question, Quiz


def _sample_course() -> CourseData:
    quiz = Quiz(
        id="quiz-1",
        title="Check your understanding",
        pass_pct=80,
        questions=[
            Question(
                id="q1",
                prompt="Which are AI use cases?",
                question_type="multi_select",
                options=[
                    {"id": "a", "text": "Summarize RFIs", "correct": True, "rationale": "Yes."},
                    {"id": "b", "text": "Pour concrete", "correct": False, "rationale": "No."},
                    {"id": "c", "text": "Draft submittals", "correct": True, "rationale": "Yes."},
                ],
            ),
            Question(
                id="q2",
                prompt="True or false: review is required.",
                question_type="true_false",
                options=[
                    {"id": "t", "text": "True", "correct": True, "rationale": "Always."},
                    {"id": "f", "text": "False", "correct": False, "rationale": "Nope."},
                ],
            ),
        ],
    )
    return CourseData(
        version_id="11111111-2222-3333-4444-555555555555",
        course_title="AI Foundations",
        label="AEC v1",
        description="A short intro course.",
        objectives=["Identify 3 AI use cases", "Explain human review"],
        lessons=[
            Lesson(
                title="Getting started",
                blocks=[
                    Block("heading", {"level": 2, "text": "Why this matters"}),
                    Block("text", {"markdown": "This is **bold**, *italic*, and `code`.\n\n- one\n- two"}),
                    Block("callout", {"variant": "warning", "markdown": "Mind the gap."}),
                    Block("image", {"alt_text": "A diagram", "caption": "Figure 1"}),
                    Block("code", {"language": "python", "source": "print('hi')", "caption": "hello"}),
                    Block("table", {"headers": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]}),
                    Block("knowledge_check", {
                        "question": "Pick the right one.",
                        "options": [
                            {"id": "a", "text": "Right", "correct": True, "feedback": "Good."},
                            {"id": "b", "text": "Wrong", "correct": False, "feedback": "Try again."},
                        ],
                        "style": "single",
                    }),
                    Block("scenario", {
                        "situation_md": "You get a vague RFI.",
                        "choices": [
                            {"id": "a", "text": "Clarify", "consequence_md": "Great.", "quality": "best"},
                            {"id": "b", "text": "Guess", "consequence_md": "Risky.", "quality": "poor"},
                        ],
                    }),
                ],
            ),
            Lesson(
                title="Assessment",
                blocks=[
                    Block("heading", {"level": 3, "text": "Final quiz"}),
                    Block("quiz_ref", {"quiz_id": "quiz-1"}, quiz=quiz),
                ],
            ),
        ],
    )


def _zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def test_package_contains_manifest_and_single_entry_point():
    data = scorm.render_package(_sample_course())
    zf = _zip(data)
    names = zf.namelist()

    assert "imsmanifest.xml" in names
    html_files = [n for n in names if n.endswith(".html")]
    assert html_files == ["index.html"], html_files

    for asset in ("styles.css", "scorm-api.js", "app.js"):
        assert asset in names

    manifest = zf.read("imsmanifest.xml").decode("utf-8")
    assert 'adlcp:scormtype="sco"' in manifest
    assert "<schemaversion>1.2</schemaversion>" in manifest
    assert 'href="index.html"' in manifest


def test_no_absolute_urls_outside_manifest():
    data = scorm.render_package(_sample_course())
    zf = _zip(data)
    for name in zf.namelist():
        if name == "imsmanifest.xml":
            continue  # schema declarations legitimately carry http(s) URLs
        body = zf.read(name).decode("utf-8")
        assert "http://" not in body, f"absolute URL in {name}"
        assert "https://" not in body, f"absolute URL in {name}"


def test_index_renders_content_and_quiz():
    data = scorm.render_package(_sample_course())
    index = _zip(data).read("index.html").decode("utf-8")

    # lessons + navigation wiring
    assert index.count('class="lesson"') == 2
    assert 'data-index="0"' in index and 'data-index="1"' in index
    assert 'src="app.js"' in index and 'src="scorm-api.js"' in index

    # block rendering
    assert "<strong>bold</strong>" in index
    assert "<em>italic</em>" in index
    assert "Knowledge check" in index
    assert 'data-quality="best"' in index

    # quiz rendering + scoring metadata
    assert 'data-quiz-id="quiz-1"' in index
    assert 'data-pass-pct="80"' in index
    assert 'data-multi="true"' in index   # multi_select question
    assert index.count('class="quiz-q"') == 2


def test_markdown_converter_basics():
    assert scorm.md_to_html("**b**") == "<p><strong>b</strong></p>"
    assert scorm.md_to_html("- a\n- b") == "<ul><li>a</li><li>b</li></ul>"
    assert scorm.md_to_html("1. a\n2. b") == "<ol><li>a</li><li>b</li></ol>"
    # HTML in content is escaped
    assert "<script>" not in scorm.md_to_html("<script>alert(1)</script>")
