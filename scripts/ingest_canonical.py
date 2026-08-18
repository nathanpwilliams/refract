"""Ingest the flagship canonical authoring files into a course.

Parses the human authoring format used in docs/flagship/*.md — the
[block-type] annotations, `adaptable=false` flags, knowledge_check /
scenario structures with ✓/✗ / [quality] markers, and <!-- authoring
note --> comments (stripped) — into the SAME canonical content-block
shapes that scripts/seed.py produces.

Target: the course "AI at Work" in the existing demo tenant, with one
canonical course_version, one module ("What AI actually is"), and the
three Module-1 lessons as content_blocks. Also upserts the "managers"
audience profile from the flagship architecture doc.

Idempotency: UPSERT-BY-TITLE, canonical UPDATED IN PLACE. The course is
matched by title (stable course_id across runs) and its single canonical
version is reused (stable version_id). Modules/lessons/content_blocks are
reconciled BY POSITION — existing rows are updated in place (same ids),
new positions inserted, trailing removed positions deleted only when
unreferenced. Because canonical block ids are preserved, existing
refraction versions (their copied blocks carry source_block_id lineage)
and their block_revisions survive re-ingestion untouched. A canonical
block that a refraction or revision still references is never deleted;
if authoring removed such a block, ingestion aborts with a clear error
rather than orphaning a refraction. No other course_versions are touched.

    source .venv/bin/activate
    python scripts/ingest_canonical.py
    python scripts/ingest_canonical.py --tenant-slug demo   # explicit

Prints the course_id, canonical version_id, and audience_profile_id.
"""
import argparse
import asyncio
import glob
import json
import os
import re
import sys

import asyncpg

sys.path.insert(0, ".")
from app.config import get_settings  # noqa: E402

TENANT_SLUG = "demo"
COURSE_TITLE = "AI at Work"
COURSE_DESCRIPTION = (
    "The flagship canonical course: role-appropriate AI literacy for "
    "professional work, refracted into audience-specific tracks.")
MODULE_TITLE = "What AI actually is"
AUTHORING_DIR = "docs/flagship"

# Canonical learning objectives (immutable through refraction), from
# docs/flagship-architecture.md. Stored on the canonical version even
# though only Module 1 is ingested so far.
OBJECTIVES = [
    "Distinguish realistic AI capabilities from limitations in professional work",
    "Describe how large language models generate output and why they can be confidently wrong",
    "Construct effective prompts for document-centric professional tasks",
    "Apply verification practices before relying on AI-generated output",
    "Identify information that must never be entered into unapproved AI tools, and the red-line tasks AI must never perform",
    "Follow your organization's AI policy: approved tools, documentation of use, and when to escalate to a human decision-maker",
]

# "managers" role track, per docs/flagship-architecture.md + task brief.
# Modeled on the AEC_PROFILE attribute shape in scripts/seed.py.
MANAGERS_PROFILE = {
    "industry": "Cross-industry (role track)",
    "roles": ["team managers", "project managers"],
    "prior_knowledge": "intermediate",
    "example_domains": ["status reports", "performance notes", "meeting synthesis"],
    "tone": "professional, peer",
    "description": (
        "Team and project managers exercising delegation-era judgment: "
        "reviewing AI-assisted work produced by others rather than "
        "producing it themselves."),
}
MANAGERS_PROFILE_NAME = "managers"

# ---------------------------------------------------------------------------
# Authoring-format parser
# ---------------------------------------------------------------------------

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
BLOCK_HEADER_RE = re.compile(r"^\[([a-z_]+)([^\]]*)\]\s*$")
LESSON_TITLE_RE = re.compile(r"^#\s*Lesson\s+([\d.]+)\s*[—-]\s*(.+)$")
MINUTES_RE = re.compile(r"~\s*(\d+)\s*min")
OPTION_RE = re.compile(r"^-\s*\(([a-z])\)\s*(.*)$")
CHECK = "✓"   # ✓
CROSS = "✗"   # ✗
ARROW = "→"   # →


def _reflow(lines: list[str]) -> str:
    """Unwrap hard-wrapped authoring text into canonical markdown.

    Blank lines separate paragraphs. Within a paragraph, wrapped lines
    are joined with a space; a paragraph whose lines include list items
    (starting with '- ') keeps one item per logical line, joining each
    item's continuation lines.
    """
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(current)

    out_paras: list[str] = []
    for para in paragraphs:
        is_list = any(ln.lstrip().startswith("- ") for ln in para)
        if is_list:
            items: list[str] = []
            for ln in para:
                stripped = ln.strip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip())
                elif items:
                    items[-1] += " " + stripped
                else:
                    items.append(stripped)  # stray leader line
            out_paras.append("\n".join(f"- {it}" for it in items))
        else:
            out_paras.append(" ".join(ln.strip() for ln in para))
    return "\n\n".join(out_paras)


def _strip_markers(text: str) -> str:
    """Remove trailing ✓/✗ correctness markers and extra whitespace."""
    return text.replace(CHECK, "").replace(CROSS, "").strip()


def _parse_knowledge_check(body: list[str]) -> dict:
    question_lines: list[str] = []
    options: list[dict] = []
    cur: dict | None = None
    mode = "question"  # question -> then per-option: text | feedback

    for raw in body:
        stripped = raw.strip()
        if not stripped:
            continue
        m = OPTION_RE.match(stripped)
        if m:
            cur = {"id": m.group(1), "_text": [m.group(2)], "_feedback": [],
                   "correct": None}
            options.append(cur)
            mode = "text"
            continue
        if cur is None:
            question_lines.append(stripped)
            continue
        if stripped[0] in (CHECK, CROSS):
            cur["correct"] = stripped[0] == CHECK
            cur["_feedback"].append(stripped[1:].strip())
            mode = "feedback"
        elif mode == "feedback":
            cur["_feedback"].append(stripped)
        else:
            cur["_text"].append(stripped)

    question = _reflow(question_lines)
    if question.startswith("Q:"):
        question = question[2:].strip()

    out_options = []
    for o in options:
        out_options.append({
            "id": o["id"],
            "text": _strip_markers(_reflow(o["_text"])),
            "correct": bool(o["correct"]),
            "feedback": _reflow(o["_feedback"]),
        })
    return {"question": question, "options": out_options, "style": "single"}


def _parse_scenario(body: list[str]) -> dict:
    QUALITY_RE = re.compile(r"^\[(best|acceptable|poor)\]\s*(.*)$")
    situation_lines: list[str] = []
    choices: list[dict] = []
    cur: dict | None = None
    mode = "situation"  # situation | text | consequence

    for raw in body:
        stripped = raw.strip()
        if not stripped:
            continue
        m = OPTION_RE.match(stripped)
        if m:
            rest = m.group(2)
            qm = QUALITY_RE.match(rest)
            quality = qm.group(1) if qm else None
            text0 = qm.group(2) if qm else rest
            cur = {"id": m.group(1), "quality": quality,
                   "_text": [text0], "_consequence": []}
            choices.append(cur)
            mode = "text"
            continue
        if cur is None:
            situation_lines.append(stripped)
            continue
        if stripped.startswith(ARROW):
            cur["_consequence"].append(stripped[1:].strip())
            mode = "consequence"
        elif mode == "consequence":
            cur["_consequence"].append(stripped)
        else:
            cur["_text"].append(stripped)

    situation = _reflow(situation_lines)
    if situation.startswith("Situation:"):
        situation = situation[len("Situation:"):].strip()

    out_choices = []
    for c in choices:
        out_choices.append({
            "id": c["id"],
            "text": _reflow(c["_text"]),
            "consequence_md": _reflow(c["_consequence"]),
            "quality": c["quality"],
        })
    return {"situation_md": situation, "choices": out_choices}


def _parse_image(body: list[str], storage_path: str) -> dict:
    alt_lines: list[str] = []
    caption_lines: list[str] = []
    target: list[str] | None = None
    for raw in body:
        stripped = raw.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith("alt:"):
            target = alt_lines
            target.append(stripped[4:].strip())
        elif low.startswith("caption:"):
            target = caption_lines
            target.append(stripped[len("caption:"):].strip())
        elif target is not None:
            target.append(stripped)
    return {
        "storage_path": storage_path,
        "alt_text": " ".join(alt_lines).strip(),
        "caption": " ".join(caption_lines).strip(),
    }


def parse_lesson(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = COMMENT_RE.sub("", text)
    lines = text.splitlines()

    # --- header: title + minutes (before content starts) ---
    title = None
    minutes = None
    number = None
    for line in lines[:6]:
        m = LESSON_TITLE_RE.match(line.strip())
        if m:
            number = m.group(1)
            # Trim trailing " — LOCKED v1.1"-style annotations.
            title = re.split(r"\s+[—-]\s+", m.group(2).strip())[0].strip()
        mm = MINUTES_RE.search(line)
        if mm and minutes is None:
            minutes = int(mm.group(1))
    if title is None:
        raise SystemExit(f"{path}: could not find a '# Lesson X.Y — Title' header")

    lesson_key = number.replace(".", "-")  # "1.1" -> "1-1"

    # --- tokenize into blocks; drop the rubric self-check footer ---
    blocks: list[dict] = []
    header = None
    body: list[str] = []
    image_count = 0

    def flush():
        nonlocal header, body, image_count
        if header is None:
            return
        tag, attrs = header
        block = _build_block(tag, attrs, body, lesson_key, image_count)
        if block is not None:
            if block["t"] == "image":
                image_count += 1
            blocks.append(block)
        header = None
        body = []

    for line in lines:
        stripped = line.strip()
        # Footer marker ends the content region.
        if stripped.startswith("**Rubric self-check:"):
            break
        m = BLOCK_HEADER_RE.match(stripped)
        if m:
            flush()
            header = (m.group(1), m.group(2).strip())
        elif header is not None:
            body.append(line)
    flush()

    return {"number": number, "position": int(number.split(".")[1]),
            "title": title, "minutes": minutes, "blocks": blocks}


def _build_block(tag: str, attrs: str, body: list[str], lesson_key: str,
                 image_count: int) -> dict | None:
    tokens = attrs.split()
    adaptable = True
    for tok in list(tokens):
        if tok == "adaptable=false":
            adaptable = False
            tokens.remove(tok)
        elif tok == "adaptable=true":
            tokens.remove(tok)

    if tag == "heading":
        level = 2
        for tok in tokens:
            hm = re.match(r"h([1-6])$", tok)
            if hm:
                level = int(hm.group(1))
        return {"t": "heading", "adaptable": adaptable,
                "c": {"level": level, "text": _reflow(body)}}
    if tag == "text":
        return {"t": "text", "adaptable": adaptable,
                "c": {"markdown": _reflow(body)}}
    if tag == "callout":
        variant = tokens[0] if tokens else "tip"
        return {"t": "callout", "adaptable": adaptable,
                "c": {"variant": variant, "markdown": _reflow(body)}}
    if tag == "image":
        storage_path = f"flagship/{lesson_key}-image-{image_count + 1}.png"
        return {"t": "image", "adaptable": adaptable,
                "c": _parse_image(body, storage_path)}
    if tag == "knowledge_check":
        return {"t": "knowledge_check", "adaptable": adaptable,
                "c": _parse_knowledge_check(body)}
    if tag == "scenario":
        return {"t": "scenario", "adaptable": adaptable,
                "c": _parse_scenario(body)}
    raise SystemExit(f"Unknown block tag: [{tag} {attrs}]")


def load_lessons() -> list[dict]:
    paths = glob.glob(os.path.join(AUTHORING_DIR, "lesson-1-*.md"))
    if not paths:
        raise SystemExit(f"No authoring files found in {AUTHORING_DIR}/lesson-1-*.md")
    lessons = [parse_lesson(p) for p in paths]
    lessons.sort(key=lambda l: l["position"])
    return lessons


# ---------------------------------------------------------------------------
# DB ingestion — upsert-by-title, canonical version updated in place
# ---------------------------------------------------------------------------

async def _block_is_referenced(conn, block_id) -> bool:
    """True if a refraction copy or a revision still depends on this
    canonical block (source_block_id lineage or block_revisions.block_id)."""
    return bool(await conn.fetchval(
        """select exists(select 1 from block_revisions where block_id = $1)
                or exists(select 1 from content_blocks where source_block_id = $1)""",
        block_id))


async def _reconcile_module(conn, tenant_id, version_id, position, title):
    existing = await conn.fetchval(
        "select id from modules where tenant_id = $1 and version_id = $2 and position = $3",
        tenant_id, version_id, position)
    if existing:
        await conn.execute("update modules set title = $2 where id = $1", existing, title)
        return existing
    return await conn.fetchval(
        """insert into modules (tenant_id, version_id, title, position)
           values ($1, $2, $3, $4) returning id""",
        tenant_id, version_id, title, position)


async def _reconcile_lesson(conn, tenant_id, module_id, position, title, minutes):
    existing = await conn.fetchval(
        "select id from lessons where tenant_id = $1 and module_id = $2 and position = $3",
        tenant_id, module_id, position)
    if existing:
        await conn.execute(
            "update lessons set title = $2, estimated_minutes = $3 where id = $1",
            existing, title, minutes)
        return existing
    return await conn.fetchval(
        """insert into lessons (tenant_id, module_id, title, position, estimated_minutes)
           values ($1, $2, $3, $4, $5) returning id""",
        tenant_id, module_id, title, position, minutes)


async def _reconcile_blocks(conn, tenant_id, lesson_id, blocks) -> None:
    """Update canonical blocks in place by position; insert new positions;
    delete trailing removed positions only when unreferenced."""
    existing = {
        r["position"]: r["id"]
        for r in await conn.fetch(
            "select id, position from content_blocks where tenant_id = $1 and lesson_id = $2",
            tenant_id, lesson_id)
    }
    for pos, block in enumerate(blocks, start=1):
        if pos in existing:
            await conn.execute(
                """update content_blocks
                     set block_type = $2, content = $3, adaptable = $4
                   where id = $1""",
                existing[pos], block["t"], json.dumps(block["c"]), block["adaptable"])
        else:
            await conn.execute(
                """insert into content_blocks
                     (tenant_id, lesson_id, position, block_type, content, adaptable)
                   values ($1, $2, $3, $4, $5, $6)""",
                tenant_id, lesson_id, pos, block["t"], json.dumps(block["c"]),
                block["adaptable"])
    for pos, block_id in existing.items():
        if pos > len(blocks):
            if await _block_is_referenced(conn, block_id):
                raise SystemExit(
                    f"Refusing to delete canonical block at lesson {lesson_id} "
                    f"position {pos}: an existing refraction or revision depends on "
                    "it. Re-authoring that removes an already-adapted block needs a "
                    "manual migration so refractions aren't orphaned.")
            await conn.execute("delete from content_blocks where id = $1", block_id)


async def _prune_trailing_lessons(conn, tenant_id, module_id, keep_max_position) -> None:
    stale = await conn.fetch(
        "select id, position from lessons where tenant_id = $1 and module_id = $2 and position > $3",
        tenant_id, module_id, keep_max_position)
    for row in stale:
        blocks = await conn.fetch(
            "select id from content_blocks where tenant_id = $1 and lesson_id = $2",
            tenant_id, row["id"])
        for b in blocks:
            if await _block_is_referenced(conn, b["id"]):
                raise SystemExit(
                    f"Refusing to delete lesson at position {row['position']}: it "
                    "contains a canonical block an existing refraction/revision "
                    "depends on. Handle this authoring change with a manual migration.")
        await conn.execute("delete from lessons where id = $1", row["id"])


async def ingest(tenant_slug: str) -> None:
    lessons = load_lessons()

    conn = await asyncpg.connect(dsn=get_settings().database_url, statement_cache_size=0)
    try:
        tenant_id = await conn.fetchval(
            "select id from tenants where slug = $1", tenant_slug)
        if tenant_id is None:
            raise SystemExit(
                f"No tenant with slug {tenant_slug!r}. Run scripts/seed.py first.")

        # created_by: reuse an existing owner/author profile if present.
        created_by = await conn.fetchval(
            """select id from profiles where tenant_id = $1
               order by (role = 'owner') desc, created_at limit 1""", tenant_id)

        async with conn.transaction():
            # --- managers audience profile: upsert by name ---
            profile_id = await conn.fetchval(
                "select id from audience_profiles where tenant_id = $1 and name = $2",
                tenant_id, MANAGERS_PROFILE_NAME)
            if profile_id:
                await conn.execute(
                    "update audience_profiles set attributes = $2 where id = $1",
                    profile_id, json.dumps(MANAGERS_PROFILE))
            else:
                profile_id = await conn.fetchval(
                    """insert into audience_profiles (tenant_id, name, attributes)
                       values ($1, $2, $3) returning id""",
                    tenant_id, MANAGERS_PROFILE_NAME, json.dumps(MANAGERS_PROFILE))

            # --- course: upsert by title (stable course_id) ---
            course_id = await conn.fetchval(
                "select id from courses where tenant_id = $1 and title = $2",
                tenant_id, COURSE_TITLE)
            if course_id:
                await conn.execute(
                    "update courses set description = $2 where id = $1",
                    course_id, COURSE_DESCRIPTION)
            else:
                course_id = await conn.fetchval(
                    """insert into courses (tenant_id, title, description, created_by)
                       values ($1, $2, $3, $4) returning id""",
                    tenant_id, COURSE_TITLE, COURSE_DESCRIPTION, created_by)

            # --- canonical version: reuse in place (stable version_id).
            # Refraction versions (is_canonical = false) are never touched. ---
            canon = await conn.fetchrow(
                """select id, status from course_versions
                   where tenant_id = $1 and course_id = $2 and is_canonical
                   order by created_at limit 1""",
                tenant_id, course_id)
            created_version = canon is None
            if canon and canon["status"] == "published":
                # A published version is immutable. In-place edits would mutate
                # content learners have already been certified against.
                raise SystemExit(
                    "canonical is published; author changes require a new "
                    "version — this protects published-version immutability")
            if canon:
                version_id = canon["id"]
                await conn.execute(
                    "update course_versions set learning_objectives = $2 where id = $1",
                    version_id, json.dumps(OBJECTIVES))
            else:
                version_id = await conn.fetchval(
                    """insert into course_versions
                         (tenant_id, course_id, is_canonical, label, status,
                          learning_objectives)
                       values ($1, $2, true, 'Canonical', 'draft', $3)
                       returning id""",
                    tenant_id, course_id, json.dumps(OBJECTIVES))

            # --- module / lessons / blocks: reconcile by position ---
            module_id = await _reconcile_module(
                conn, tenant_id, version_id, 1, MODULE_TITLE)

            block_counts: list[tuple[str, int]] = []
            for lesson in lessons:
                lesson_id = await _reconcile_lesson(
                    conn, tenant_id, module_id, lesson["position"],
                    lesson["title"], lesson["minutes"])
                await _reconcile_blocks(conn, tenant_id, lesson_id, lesson["blocks"])
                block_counts.append((lesson["title"], len(lesson["blocks"])))
            await _prune_trailing_lessons(
                conn, tenant_id, module_id,
                keep_max_position=max(l["position"] for l in lessons))

            # --- verification (inside the tx, before commit) ---
            total_blocks = await conn.fetchval(
                """select count(*) from content_blocks cb
                   join lessons l on l.id = cb.lesson_id
                   where l.module_id = $1""", module_id)
            non_adaptable = await conn.fetch(
                """select cb.block_type, cb.content, l.title as lesson_title,
                          cb.position
                   from content_blocks cb
                   join lessons l on l.id = cb.lesson_id
                   where l.module_id = $1 and cb.adaptable = false
                   order by l.position, cb.position""", module_id)

        # --- report ---
        action = "created" if created_version else "updated in place"
        print(f"Ingested successfully (canonical version {action}).\n")
        print("Block counts per lesson:")
        for title, n in block_counts:
            print(f"  {n:>3}  {title}")
        print(f"  ---  total: {total_blocks}\n")

        print(f"adaptable=false blocks: {len(non_adaptable)}")
        for row in non_adaptable:
            content = json.loads(row["content"])
            preview = content.get("markdown", content.get("text", ""))[:60]
            print(f"  [{row['block_type']}] in {row['lesson_title']!r} "
                  f"pos {row['position']}: {preview!r}")
        print()

        print(f"  course_id           = {course_id}")
        print(f"  canonical_version   = {version_id}")
        print(f"  audience_profile_id = {profile_id}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", default=TENANT_SLUG,
                        help=f"tenant to ingest into (default: {TENANT_SLUG!r})")
    args = parser.parse_args()
    asyncio.run(ingest(args.tenant_slug))
