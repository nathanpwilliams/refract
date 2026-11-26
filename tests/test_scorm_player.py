"""Browser tests for the packaged SCORM player's completion rules.

tests/test_scorm.py covers the rendering layer — what HTML comes out. This
covers the half that only exists at runtime: app.js, driven in a real
browser against a real rendered package, with a fake LMS API injected so
the SCORM calls themselves can be asserted.

The rule under test (the one a learner feels):

    a final lesson WITH a quiz    completes when the quiz is passed
    a final lesson WITHOUT a quiz completes via a "Finish course" button

Both exist because a lesson is marked visited by LEAVING it, and nobody
ever leaves the last one — without one of these two, a course could be
read end to end and never report completion.

Requires playwright and its chromium build:

    pip install playwright && python -m playwright install chromium

Skipped, not failed, when either is missing, so the suite still runs on a
machine without a browser.
"""
import io
import json
import zipfile

import pytest

from app.services import scorm
from app.services.scorm import Block, CourseData, Lesson, Question, Quiz

sync_playwright = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed").sync_playwright


# A fake SCORM 1.2 LMS: records every call so the test can assert on what
# the player reported, instead of guessing from the DOM. __STORE__ is the
# data the LMS already holds, which is how a resume is simulated.
FAKE_LMS = """
window.__calls = [];
window.__store = __STORE__;
window.API = {
  LMSInitialize: function () { window.__calls.push(["init"]); return "true"; },
  LMSFinish: function () { window.__calls.push(["finish"]); return "true"; },
  LMSGetValue: function (k) { window.__calls.push(["get", k]); return window.__store[k] || ""; },
  LMSSetValue: function (k, v) { window.__store[k] = v; window.__calls.push(["set", k, v]); return "true"; },
  LMSCommit: function () { window.__calls.push(["commit"]); return "true"; },
  LMSGetLastError: function () { return "0"; },
  LMSGetErrorString: function () { return ""; },
  LMSGetDiagnostic: function () { return ""; }
};
"""


def _lesson(title: str, *, quiz: Quiz | None = None) -> Lesson:
    blocks = [
        Block("heading", {"level": 2, "text": title}),
        Block("text", {"markdown": f"Body copy for {title}."}),
    ]
    if quiz is not None:
        blocks.append(Block("quiz_ref", {"quiz_id": quiz.id}, quiz=quiz))
    return Lesson(title=title, blocks=blocks)


def _quiz() -> Quiz:
    return Quiz(
        id="quiz-1", title="Final check", pass_pct=50,
        questions=[Question(
            id="q1", prompt="Pick the right one.", question_type="multiple_choice",
            options=[
                {"id": "a", "text": "Right", "correct": True, "rationale": "Yes."},
                {"id": "b", "text": "Wrong", "correct": False, "rationale": "No."},
            ])],
    )


def _course(*, with_quiz: bool) -> CourseData:
    """Three lessons, mirroring the flagship module's shape."""
    quiz = _quiz() if with_quiz else None
    return CourseData(
        version_id="11111111-2222-3333-4444-555555555555",
        course_title="AI at Work",
        label="managers",
        description="A three-lesson module.",
        objectives=["Finish the course"],
        lessons=[_lesson("A tool, not a colleague"),
                 _lesson("How language models actually work"),
                 _lesson("What this means for your work", quiz=quiz)],
    )


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"chromium not installed for playwright: {exc}")
        yield b
        b.close()


@pytest.fixture
def player(browser, tmp_path_factory):
    """Render a package to disk and open it, with a fake LMS attached."""
    def _open(*, with_quiz: bool, connected: bool = True, seed: dict | None = None):
        out = tmp_path_factory.mktemp("package")
        data = scorm.render_package(_course(with_quiz=with_quiz))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(out)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        if connected:
            page.add_init_script(FAKE_LMS.replace("__STORE__", json.dumps(seed or {})))
        page.goto((out / "index.html").as_uri())
        page.wait_for_selector(".lesson:not([hidden])")
        return page, errors
    return _open


def _sets(page) -> dict:
    """The last value the player reported for each SCORM element."""
    calls = page.evaluate("window.__calls || []")
    out = {}
    for call in calls:
        if call[0] == "set":
            out[call[1]] = call[2]
    return out


def _commits(page) -> int:
    return len([c for c in page.evaluate("window.__calls || []") if c[0] == "commit"])


def _advance_to_last(page) -> None:
    total = page.locator(".lesson").count()
    for _ in range(total - 1):
        page.locator(".nav-next").click()
        page.wait_for_timeout(50)


# ------------------------------------------------- quiz-less final lesson

def test_a_quiz_less_last_lesson_offers_a_finish_button(player):
    page, errors = player(with_quiz=False)
    _advance_to_last(page)

    finish = page.locator(".finish-course")
    assert finish.count() == 1, "no Finish course button on a quiz-less last lesson"
    assert finish.is_visible()
    assert page.locator(".nav-next").is_hidden(), "Next is offered past the last lesson"
    # Before finishing, the last lesson has not been earned.
    assert page.locator(".visited-count").inner_text() == "2"
    assert page.locator(".course-done").is_hidden()
    assert errors == []


def test_finishing_completes_the_course_and_reports_it(player):
    page, errors = player(with_quiz=False)
    _advance_to_last(page)
    page.locator(".finish-course").click()
    page.wait_for_timeout(50)

    assert page.locator(".visited-count").inner_text() == "3"
    assert page.locator(".course-done").is_visible()
    assert "Course complete" in page.locator(".course-done").inner_text()
    # the completion and commit logic actually ran against the LMS
    assert _sets(page)["cmi.core.lesson_status"] == "completed"
    assert _commits(page) >= 1
    suspend = json.loads(_sets(page)["cmi.suspend_data"])
    assert sorted(suspend["completed_lessons"]) == [0, 1, 2]
    assert errors == []


def test_the_finish_button_is_keyboard_reachable_and_settles(player):
    page, _ = player(with_quiz=False)
    _advance_to_last(page)
    page.locator(".finish-course").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(50)

    assert page.locator(".course-done").is_visible()
    # It does not invite a second, meaningless click.
    assert page.locator(".finish-course").is_disabled()


def test_a_read_through_without_finishing_stays_incomplete(player):
    page, _ = player(with_quiz=False)
    _advance_to_last(page)

    assert page.locator(".course-done").is_hidden()
    assert _sets(page)["cmi.core.lesson_status"] == "incomplete"


def test_completion_survives_a_resume(player):
    """Finishing, then relaunching, must not un-complete the course.

    Resume is a fresh launch against an LMS that already holds the suspend
    data — which is exactly what the second page here is given.
    """
    page, _ = player(with_quiz=False)
    _advance_to_last(page)
    page.locator(".finish-course").click()
    page.wait_for_timeout(50)
    stored = page.evaluate("window.__store")
    assert "cmi.suspend_data" in stored

    resumed, errors = player(with_quiz=False, seed=stored)

    assert resumed.locator(".visited-count").inner_text() == "3"
    assert resumed.locator(".course-done").is_visible()
    assert resumed.locator(".finish-course").is_disabled()
    assert errors == []


# ------------------------------------------------------ quiz final lesson

def test_a_last_lesson_with_a_quiz_completes_on_passing_it(player):
    page, errors = player(with_quiz=True)
    _advance_to_last(page)

    assert page.locator(".finish-course").count() == 0, \
        "a quiz-bearing last lesson must complete on the quiz, not a button"
    assert page.locator(".visited-count").inner_text() == "2"

    page.locator('.quiz .opt[data-correct="true"]').first.click()
    page.locator(".submit-quiz").click()
    page.wait_for_timeout(50)

    assert page.locator(".visited-count").inner_text() == "3"
    assert page.locator(".course-done").is_visible()
    sets = _sets(page)
    assert sets["cmi.core.lesson_status"] == "passed"
    assert sets["cmi.core.score.raw"] == "100"
    assert _commits(page) >= 1
    assert errors == []


def test_failing_the_final_quiz_does_not_complete_the_course(player):
    page, _ = player(with_quiz=True)
    _advance_to_last(page)

    page.locator('.quiz .opt[data-correct="false"]').first.click()
    page.locator(".submit-quiz").click()
    page.wait_for_timeout(50)

    assert page.locator(".visited-count").inner_text() == "2"
    assert page.locator(".course-done").is_hidden()
    assert _sets(page)["cmi.core.lesson_status"] == "incomplete"


# ------------------------------------------------------------- preview mode

def test_the_course_completes_with_no_lms_at_all(player):
    """Preview mode: the demo site serves exactly this, with no LMS."""
    page, errors = player(with_quiz=False, connected=False)

    assert page.locator(".preview-badge").is_visible()
    _advance_to_last(page)
    page.locator(".finish-course").click()
    page.wait_for_timeout(50)

    assert page.locator(".visited-count").inner_text() == "3"
    assert page.locator(".course-done").is_visible()
    assert errors == []
