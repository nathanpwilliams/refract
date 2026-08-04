"""SCORM 1.2 packager.

Turns a published course version into a self-contained, single-SCO SCORM 1.2
zip that imports into any compliant LMS and reports completion + score.

Two layers, deliberately split:
  * ``build_package`` — tenant-scoped DB reads (service role, like everything
    in ``app/services``) that assemble a plain ``CourseData`` structure.
  * ``render_package`` — pure, DB-free: turns ``CourseData`` + the static
    assets in ``app/templates/scorm`` into the zip bytes. This is what the
    unit test exercises without a database.

The static HTML mirrors ``web/components/blocks/Block.tsx`` and
``web/components/QuizRunner.tsx`` block-for-block; interactivity + SCORM
runtime live in the packaged ``app.js`` / ``scorm-api.js``.
"""
from __future__ import annotations

import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app import db

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "scorm"
STATIC_ASSETS = ("styles.css", "scorm-api.js", "app.js")


# ----------------------------------------------------------------- data model

@dataclass
class Question:
    id: str
    prompt: str
    question_type: str
    options: list[dict]  # [{id, text, correct, rationale}]


@dataclass
class Quiz:
    id: str
    title: str
    pass_pct: int
    questions: list[Question]


@dataclass
class Block:
    block_type: str
    content: dict
    quiz: Quiz | None = None


@dataclass
class Lesson:
    title: str
    blocks: list[Block]


@dataclass
class CourseData:
    version_id: str
    course_title: str
    label: str
    description: str | None
    objectives: list[str] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)


# ----------------------------------------------------------------- DB loading

async def build_package(version_id: str, tenant_id: str) -> bytes:
    """Load a version (tenant-scoped) and return the SCORM zip as bytes."""
    course = await _load_course(version_id, tenant_id)
    return render_package(course)


async def _load_course(version_id: str, tenant_id: str) -> CourseData:
    ver = await db.fetchrow_tenant(
        tenant_id,
        """select cv.label, cv.learning_objectives, c.title, c.description
           from course_versions cv
           join courses c on c.id = cv.course_id and c.tenant_id = cv.tenant_id
           where cv.tenant_id = $1 and cv.id = $2""",
        version_id,
    )
    if ver is None:
        raise LookupError("version not found")

    objectives = json.loads(ver["learning_objectives"] or "[]")

    lesson_rows = await db.fetch_tenant(
        tenant_id,
        """select l.id, l.title
           from modules m
           join lessons l on l.module_id = m.id and l.tenant_id = m.tenant_id
           where m.tenant_id = $1 and m.version_id = $2
           order by m.position, l.position""",
        version_id,
    )

    lessons: list[Lesson] = []
    quiz_cache: dict[str, Quiz] = {}
    for lr in lesson_rows:
        block_rows = await db.fetch_tenant(
            tenant_id,
            """select block_type, content from content_blocks
               where tenant_id = $1 and lesson_id = $2
               order by position""",
            str(lr["id"]),
        )
        blocks: list[Block] = []
        for br in block_rows:
            content = json.loads(br["content"])
            quiz = None
            if br["block_type"] == "quiz_ref":
                quiz_id = str(content.get("quiz_id"))
                if quiz_id not in quiz_cache:
                    loaded = await _load_quiz(quiz_id, tenant_id)
                    if loaded is not None:
                        quiz_cache[quiz_id] = loaded
                quiz = quiz_cache.get(quiz_id)
            blocks.append(Block(block_type=br["block_type"], content=content, quiz=quiz))
        lessons.append(Lesson(title=lr["title"], blocks=blocks))

    return CourseData(
        version_id=str(version_id),
        course_title=ver["title"],
        label=ver["label"],
        description=ver["description"],
        objectives=objectives,
        lessons=lessons,
    )


async def _load_quiz(quiz_id: str, tenant_id: str) -> Quiz | None:
    q = await db.fetchrow_tenant(
        tenant_id,
        "select id, title, pass_pct from quizzes where tenant_id = $1 and id = $2",
        quiz_id,
    )
    if q is None:
        return None
    qrows = await db.fetch_tenant(
        tenant_id,
        """select id, question_type, prompt, options from questions
           where tenant_id = $1 and quiz_id = $2 order by position""",
        quiz_id,
    )
    questions = [
        Question(
            id=str(r["id"]),
            prompt=r["prompt"],
            question_type=r["question_type"],
            options=json.loads(r["options"]),
        )
        for r in qrows
    ]
    return Quiz(id=str(q["id"]), title=q["title"], pass_pct=q["pass_pct"], questions=questions)


# ----------------------------------------------------------------- rendering

def render_package(course: CourseData) -> bytes:
    """Pure: assemble the zip bytes from CourseData + packaged static assets."""
    index_html = _render_index(course)
    manifest = _render_manifest(course)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", manifest)
        zf.writestr("index.html", index_html)
        for name in STATIC_ASSETS:
            zf.writestr(name, (TEMPLATE_DIR / name).read_text(encoding="utf-8"))
    return buf.getvalue()


def _render_index(course: CourseData) -> str:
    template = (TEMPLATE_DIR / "page.html").read_text(encoding="utf-8")
    title = f"{course.course_title} — {course.label} edition"

    desc = f'<p class="course-desc">{esc(course.description)}</p>' if course.description else ""
    objectives = _render_objectives(course.objectives)
    menu = "\n".join(_render_menu_item(i, l) for i, l in enumerate(course.lessons))
    lessons = "\n".join(_render_lesson(i, l) for i, l in enumerate(course.lessons))

    replacements = {
        "{{TITLE}}": esc(title),
        "{{LABEL}}": esc(course.label),
        "{{COURSE_TITLE}}": esc(course.course_title),
        "{{COURSE_DESC}}": desc,
        "{{TOTAL}}": str(len(course.lessons)),
        "{{OBJECTIVES}}": objectives,
        "{{MENU}}": menu,
        "{{LESSONS}}": lessons,
    }
    out = template
    for token, value in replacements.items():
        out = out.replace(token, value)
    return out


def _render_objectives(objectives: list[str]) -> str:
    if not objectives:
        return ""
    items = "".join(f"<li>{esc(o)}</li>" for o in objectives)
    return (
        '<section class="objectives">'
        "<h2>What you&rsquo;ll be able to do</h2>"
        f"<ul>{items}</ul>"
        "</section>"
    )


def _render_menu_item(i: int, lesson: Lesson) -> str:
    return (
        '<li><button type="button" class="menu-item" '
        f'data-index="{i}" aria-current="false">'
        f'<span class="menu-title">{esc(lesson.title)}</span>'
        '<span class="menu-status">Not visited</span>'
        "</button></li>"
    )


def _render_lesson(i: int, lesson: Lesson) -> str:
    body = "".join(_render_block(b) for b in lesson.blocks)
    return (
        f'<section class="lesson" data-index="{i}" aria-labelledby="lh-{i}" hidden>'
        f'<h2 id="lh-{i}">{esc(lesson.title)}</h2>'
        f"{body}"
        "</section>"
    )


# ----------------------------------------------------------------- blocks

def _render_block(block: Block) -> str:
    c = block.content
    t = block.block_type
    if t == "heading":
        level = c.get("level", 2)
        tag = "h3" if isinstance(level, int) and level <= 2 else "h4"
        return f"<{tag} class=\"block\">{esc(c.get('text', ''))}</{tag}>"
    if t == "text":
        return f'<div class="block prose">{md_to_html(str(c.get("markdown", "")))}</div>'
    if t == "callout":
        return _render_callout(c)
    if t == "image":
        return _render_image(c)
    if t == "code":
        return _render_code(c)
    if t == "table":
        return _render_table(c)
    if t == "video_embed":
        return _render_video(c)
    if t == "knowledge_check":
        return _render_knowledge_check(c)
    if t == "scenario":
        return _render_scenario(c)
    if t == "quiz_ref":
        return _render_quiz(block.quiz) if block.quiz else ""
    return ""


def _render_callout(c: dict) -> str:
    variant = str(c.get("variant", "tip"))
    labels = {"key_idea": "Key idea", "tip": "Tip", "warning": "Warning"}
    if variant not in labels:
        variant = "tip"
    return (
        f'<aside class="block callout {variant}">'
        f'<p class="callout-label">{labels[variant]}</p>'
        f'<div class="prose">{md_to_html(str(c.get("markdown", "")))}</div>'
        "</aside>"
    )


def _render_image(c: dict) -> str:
    caption = c.get("caption")
    cap = f"<figcaption>{esc(caption)}</figcaption>" if isinstance(caption, str) and caption else ""
    return (
        '<figure class="block image-ph">'
        f'<p>[Image] {esc(c.get("alt_text", ""))}</p>'
        f"{cap}"
        "</figure>"
    )


def _render_code(c: dict) -> str:
    caption = c.get("caption")
    cap = f'<p class="code-caption">{esc(caption)}</p>' if isinstance(caption, str) and caption else ""
    return (
        '<div class="block">'
        f'<pre class="code"><code>{esc(c.get("source", ""))}</code></pre>'
        f"{cap}"
        "</div>"
    )


def _render_table(c: dict) -> str:
    headers = c.get("headers") or []
    rows = c.get("rows") or []
    caption = c.get("caption")
    cap = f"<caption>{esc(caption)}</caption>" if isinstance(caption, str) and caption else ""
    thead = "".join(f"<th>{esc(h)}</th>" for h in headers)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<table class="data block">'
        f"{cap}<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody>"
        "</table>"
    )


def _render_video(c: dict) -> str:
    url = str(c.get("url", ""))
    title = esc(c.get("title") or "Video")
    link = f'<a href="{esc(url)}">{title}</a>' if url else title
    transcript = c.get("transcript_md")
    details = ""
    if isinstance(transcript, str) and transcript:
        details = (
            "<details><summary>Transcript</summary>"
            f'<div class="prose">{md_to_html(transcript)}</div></details>'
        )
    return f'<figure class="block video"><p class="video-title">{link}</p>{details}</figure>'


def _render_knowledge_check(c: dict) -> str:
    options = c.get("options") or []
    opts = "".join(
        '<button type="button" class="opt" role="radio" aria-checked="false" '
        f'data-oid="{esc(o.get("id", ""))}" '
        f'data-correct="{"true" if o.get("correct") else "false"}" '
        f'data-feedback="{esc(o.get("feedback", ""))}">{esc(o.get("text", ""))}</button>'
        for o in options
    )
    return (
        '<div class="block interactive kc">'
        '<p class="eyebrow">Knowledge check</p>'
        f'<p class="prompt">{esc(c.get("question", ""))}</p>'
        '<div class="options" role="radiogroup" aria-label="Knowledge check options">'
        f"{opts}</div>"
        '<div class="feedback" role="status" hidden></div>'
        "</div>"
    )


def _render_scenario(c: dict) -> str:
    choices = c.get("choices") or []
    btns = "".join(
        '<button type="button" class="choice" '
        f'data-cid="{esc(ch.get("id", ""))}" '
        f'data-quality="{esc(ch.get("quality", "poor"))}" '
        f'aria-pressed="false">{esc(ch.get("text", ""))}</button>'
        for ch in choices
    )
    cons = "".join(
        f'<div data-cid="{esc(ch.get("id", ""))}" data-quality="{esc(ch.get("quality", "poor"))}">'
        f'{md_to_html(str(ch.get("consequence_md", "")))}</div>'
        for ch in choices
    )
    return (
        '<div class="block interactive scenario">'
        '<p class="eyebrow">Scenario</p>'
        f'<div class="situation prose">{md_to_html(str(c.get("situation_md", "")))}</div>'
        f'<div class="choices">{btns}</div>'
        '<div class="feedback" role="status" hidden></div>'
        f'<div class="consequences" hidden>{cons}</div>'
        "</div>"
    )


def _render_quiz(quiz: Quiz) -> str:
    questions = ""
    for i, q in enumerate(quiz.questions):
        multi = q.question_type == "multi_select"
        hint = '<span class="hint">(select all that apply)</span>' if multi else ""
        opts = "".join(
            '<button type="button" class="opt" '
            f'data-oid="{esc(o.get("id", ""))}" '
            f'data-correct="{"true" if o.get("correct") else "false"}" '
            f'aria-pressed="false">{esc(o.get("text", ""))}</button>'
            f'<p class="rationale" hidden>{esc(o.get("rationale", ""))}</p>'
            for o in q.options
        )
        questions += (
            f'<li class="quiz-q" data-qid="{esc(q.id)}" data-multi="{"true" if multi else "false"}">'
            f'<p class="prompt">{i + 1}. {esc(q.prompt)} {hint}</p>'
            f'<div class="options">{opts}</div>'
            "</li>"
        )
    return (
        f'<form class="quiz" data-quiz-id="{esc(quiz.id)}" data-pass-pct="{int(quiz.pass_pct)}">'
        f"<h3>{esc(quiz.title)}</h3>"
        f'<p class="muted">Passing score: {int(quiz.pass_pct)}%</p>'
        f'<ol class="questions">{questions}</ol>'
        '<div class="quiz-actions">'
        '<button type="button" class="btn btn-primary submit-quiz">Submit answers</button>'
        '<p class="quiz-result" role="status" hidden></p>'
        "</div></form>"
    )


# ----------------------------------------------------------------- manifest

def _render_manifest(course: CourseData) -> str:
    org_title = esc(f"{course.course_title} — {course.label} edition")
    files = "".join(
        f'      <file href="{name}"/>\n'
        for name in ("index.html", *STATIC_ASSETS)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<manifest identifier="MANIFEST-{esc(course.version_id)}" version="1.0"\n'
        '  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"\n'
        '  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"\n'
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '  xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd\n'
        '                      http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">\n'
        "  <metadata>\n"
        "    <schema>ADL SCORM</schema>\n"
        "    <schemaversion>1.2</schemaversion>\n"
        "  </metadata>\n"
        '  <organizations default="ORG-default">\n'
        '    <organization identifier="ORG-default">\n'
        f"      <title>{org_title}</title>\n"
        '      <item identifier="ITEM-1" identifierref="RES-1" isvisible="true">\n'
        f"        <title>{org_title}</title>\n"
        "      </item>\n"
        "    </organization>\n"
        "  </organizations>\n"
        "  <resources>\n"
        '    <resource identifier="RES-1" type="webcontent" '
        'adlcp:scormtype="sco" href="index.html">\n'
        f"{files}"
        "    </resource>\n"
        "  </resources>\n"
        "</manifest>\n"
    )


# ----------------------------------------------------------------- markdown

def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def md_to_html(text: str) -> str:
    """Minimal build-time markdown: paragraphs, bold/italic, inline code,
    links, and unordered/ordered lists. No client-side parsing."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    out: list[str] = []
    for blk in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in blk.split("\n") if ln.strip()]
        if lines and all(re.match(r"^[-*+]\s+", ln) for ln in lines):
            items = "".join(f"<li>{_inline(re.sub(r'^[-*+]\s+', '', ln))}</li>" for ln in lines)
            out.append(f"<ul>{items}</ul>")
        elif lines and all(re.match(r"^\d+\.\s+", ln) for ln in lines):
            items = "".join(f"<li>{_inline(re.sub(r'^\d+\.\s+', '', ln))}</li>" for ln in lines)
            out.append(f"<ol>{items}</ol>")
        else:
            out.append(f"<p>{_inline(' '.join(lines))}</p>")
    return "".join(out)


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: f'<code class="inline">{codes[int(m.group(1))]}</code>', s)
    return s
