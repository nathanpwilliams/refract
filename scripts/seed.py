"""Seed script — demo tenant + canonical mini-course + AEC audience profile.

Seeds the GENERIC canonical course ("Practical AI for Professionals"); the
AEC version is what the adaptation pipeline produces from it. That's the
demo: same canonical content, audience-specific course out the other end.

Prerequisite: create one user in the Supabase dashboard first
(Authentication → Users → Add user), because profiles has a foreign key to
auth.users. Then run:

    python scripts/seed.py --author-email you@example.com
    python scripts/seed.py --author-email you@example.com --reset   # wipe + reseed

On success it prints the IDs and a ready-to-paste curl for kicking off an
adaptation job.
"""
import argparse
import asyncio
import json
import sys

import asyncpg

sys.path.insert(0, ".")
from app.config import get_settings  # noqa: E402

TENANT_SLUG = "demo"

OBJECTIVES = [
    "Distinguish realistic AI capabilities from limitations in professional work",
    "Describe how large language models generate output and why they can be wrong",
    "Construct effective prompts for document-centric professional tasks",
    "Apply verification practices before relying on AI-generated output",
]

AEC_PROFILE = {
    "industry": "Architecture, Engineering & Construction",
    "roles": ["architects", "project managers", "construction managers"],
    "prior_knowledge": "novice",
    "example_domains": [
        "BIM coordination", "RFI and submittal triage", "spec section review",
        "site photo documentation", "schedule narratives",
    ],
    "vocabulary": {
        "prefer": ["design phase", "submittal", "punch list", "as-built"],
        "avoid": ["sprint", "backlog", "growth hacking"],
    },
    "tone": "professional, plain language",
    "compliance_notes": "Section 508 accessible; no vendor endorsements",
}

# (module_title, [(lesson_title, minutes, [blocks])])
COURSE = [
    ("Understanding AI fundamentals", [
        ("What AI can and can't do", 12, [
            {"t": "heading", "c": {"level": 2, "text": "A tool, not a colleague"}},
            {"t": "text", "c": {"markdown": (
                "Modern AI systems are pattern-recognition engines trained on enormous "
                "amounts of text and images. They are remarkably good at tasks that look "
                "like things they have seen before: summarizing documents, drafting routine "
                "correspondence, extracting structured information from unstructured files, "
                "and answering questions about material you give them.\n\n"
                "They are unreliable at tasks that require guaranteed accuracy without "
                "verification: citing sources, performing multi-step calculations, or "
                "reporting facts about events after their training data ends. The core "
                "professional skill is matching the task to the tool.")}},
            {"t": "callout", "c": {"variant": "key_idea", "markdown": (
                "**Rule of thumb:** use AI where a fast, good draft beats a slow, perfect "
                "one — and where you will review the output before it matters.")}},
            {"t": "text", "c": {"markdown": (
                "Consider three everyday tasks. Summarizing a 40-page report into one page: "
                "excellent fit, because you can skim the source to spot-check. Drafting a "
                "client status update from bullet points: strong fit, because you review "
                "every word before sending. Calculating a fee schedule: poor fit, because "
                "an error is invisible until it costs money — use a spreadsheet and let AI "
                "explain the formula instead.")}},
            {"t": "image", "c": {
                "storage_path": "seed/capability-spectrum.png",
                "alt_text": ("Horizontal spectrum diagram showing AI task fit from strong "
                             "(drafting, summarizing) to weak (precise calculation, "
                             "unverified facts)"),
                "caption": "Where AI assistance fits on a professional task spectrum."}},
            {"t": "knowledge_check", "c": {
                "question": ("A colleague wants AI to calculate next quarter's fee "
                             "schedule. Best response?"),
                "options": [
                    {"id": "a", "text": "Good use — AI is fast with numbers",
                     "correct": False,
                     "feedback": ("Calculation errors are invisible until they cost "
                                  "money — this is the classic poor fit.")},
                    {"id": "b",
                     "text": ("Use a spreadsheet for the math; let AI explain or "
                              "document the formula"),
                     "correct": True,
                     "feedback": ("Right — verified tools for the numbers, AI for the "
                                  "explanation around them.")},
                    {"id": "c", "text": "Never use AI near anything financial",
                     "correct": False,
                     "feedback": ("Too broad — AI can safely draft the narrative and "
                                  "explain formulas; it just shouldn't produce "
                                  "unverified figures.")},
                ],
                "style": "single"}},
        ]),
        ("How language models actually work", 15, [
            {"t": "heading", "c": {"level": 2, "text": "Prediction, not lookup"}},
            {"t": "text", "c": {"markdown": (
                "A large language model does not look up answers in a database. It "
                "generates text one small piece at a time, each time predicting what is "
                "most likely to come next given everything so far. Training on vast text "
                "collections makes those predictions remarkably fluent and often accurate "
                "— but *plausible* is the target, not *true*.\n\n"
                "This single fact explains most AI behavior you will encounter: why "
                "answers sound confident even when wrong, why the same question can get "
                "different answers, and why models sometimes invent details — a failure "
                "mode called hallucination.")}},
            # Deliberately locked: definitions must read identically in every
            # audience version. Exercises the adaptable=false path.
            {"t": "text", "adaptable": False, "c": {"markdown": (
                "**Definitions used throughout this course**\n\n"
                "- *Model*: the trained system that generates output.\n"
                "- *Prompt*: everything you provide to the model — instructions, "
                "questions, and reference material.\n"
                "- *Context window*: the maximum amount of text a model can consider "
                "at once.\n"
                "- *Hallucination*: fluent, confident output that is factually wrong "
                "or fabricated.")}},
            {"t": "callout", "c": {"variant": "warning", "markdown": (
                "A confident tone is not evidence of accuracy. The model produces the "
                "same fluent style whether it is right or wrong.")}},
        ]),
    ]),
    ("Applying AI in your work", [
        ("Writing effective prompts", 15, [
            {"t": "heading", "c": {"level": 2, "text": "Context, task, format"}},
            {"t": "text", "c": {"markdown": (
                "Strong prompts share three ingredients. **Context**: the background the "
                "model needs — paste the relevant document, describe the audience, state "
                "constraints. **Task**: one clear instruction, not three tangled ones. "
                "**Format**: what the output should look like — a table, a one-page memo, "
                "a bulleted list with citations to the provided text.\n\n"
                "Compare: \"summarize this report\" versus \"summarize the attached "
                "10-page progress report for an executive audience in under 200 words, "
                "highlighting schedule risks and any open decisions.\" The second takes "
                "twenty seconds longer to write and saves three rounds of revision.")}},
            {"t": "callout", "c": {"variant": "tip", "markdown": (
                "Iterate instead of restarting. If the first draft misses, say what to "
                "change — \"shorter, and lead with the risk item\" — rather than "
                "rewriting the prompt from scratch.")}},
            {"t": "scenario", "c": {
                "situation_md": (
                    "It's 4:50 PM. Your director asks for a one-page summary of a "
                    "30-page vendor proposal by tomorrow morning. You open your AI "
                    "assistant. What's your move?"),
                "choices": [
                    {"id": "a",
                     "text": "Type: \"Summarize this vendor proposal\" and attach it",
                     "consequence_md": (
                         "You get a generic summary that buries the two things your "
                         "director actually cares about. Workable, but you'll spend "
                         "20 minutes reworking it — the prompt did not say who it's "
                         "for or what matters."),
                     "quality": "acceptable"},
                    {"id": "b",
                     "text": ("Attach it and type: \"One-page summary for a director "
                              "deciding whether to shortlist this vendor — lead with "
                              "pricing terms and delivery risks, plain language, "
                              "bullet the open questions\""),
                     "consequence_md": (
                         "Context, task, and format in one prompt. The draft comes "
                         "back needing minor edits, and the open-questions list "
                         "makes you look thorough. This is the pattern."),
                     "quality": "best"},
                    {"id": "c",
                     "text": ("Ask the AI what the proposal says from memory — no "
                              "attachment, it's probably seen this vendor before"),
                     "consequence_md": (
                         "The model has never seen this proposal. It will produce "
                         "confident, plausible, fabricated specifics — and you're "
                         "handing them to your director. Always provide the source."),
                     "quality": "poor"},
                ]}},
            {"t": "text", "c": {"markdown": (
                "One more habit that pays off: give the model your source material "
                "rather than trusting its memory. Asking \"what does section 3 of this "
                "contract say about delays?\" with the contract pasted in is verification-"
                "friendly; asking the same question from memory invites hallucination.")}},
        ]),
        ("Verifying output and managing risk", 12, [
            {"t": "heading", "c": {"level": 2, "text": "Trust, but verify — then verify"}},
            {"t": "text", "c": {"markdown": (
                "Adopt a simple gate before AI output leaves your hands: **facts checked** "
                "(names, numbers, dates, citations traced to a source), **judgment "
                "reviewed** (would you sign this?), and **confidentiality respected** "
                "(nothing sensitive pasted into tools your organization has not "
                "approved).\n\n"
                "Match scrutiny to stakes. A brainstorming list needs a skim. A document "
                "going to a client, a regulator, or a public record needs line-by-line "
                "review — the time saved is in the drafting, not the checking.")}},
            {"t": "callout", "c": {"variant": "key_idea", "markdown": (
                "You are accountable for everything you send, whoever — or whatever — "
                "drafted it.")}},
            {"t": "image", "c": {
                "storage_path": "seed/verification-gate.png",
                "alt_text": ("Three-step verification gate diagram: facts checked, "
                             "judgment reviewed, confidentiality respected"),
                "caption": "The three-part gate before AI output leaves your hands."}},
        ]),
    ]),
]

QUIZ = {
    "title": "Course check: practical AI",
    "pass_pct": 80,
    "questions": [
        {
            "question_type": "multiple_choice",
            "prompt": ("A colleague asks an AI assistant a factual question without "
                       "providing any source material, and receives a confident, detailed "
                       "answer. What is the most accurate assessment?"),
            "objective_ref": 1,
            "options": [
                {"id": "a", "text": "The answer is reliable because it is detailed and confident",
                 "correct": False,
                 "rationale": "Fluency and confidence are style, not evidence — the model sounds the same when wrong."},
                {"id": "b", "text": "The answer is plausible but unverified and may contain fabricated details",
                 "correct": True,
                 "rationale": "Models predict likely text rather than looking up facts, so unverified answers can hallucinate."},
                {"id": "c", "text": "The answer is wrong, because AI cannot answer factual questions",
                 "correct": False,
                 "rationale": "Models often answer factual questions correctly; the issue is that accuracy is not guaranteed."},
                {"id": "d", "text": "The answer is reliable if the assistant is a recent model",
                 "correct": False,
                 "rationale": "Newer models hallucinate less but the failure mode remains; verification is still required."},
            ],
        },
        {
            "question_type": "multiple_choice",
            "prompt": ("Which task is the WEAKEST fit for AI assistance without additional "
                       "safeguards?"),
            "objective_ref": 0,
            "options": [
                {"id": "a", "text": "Summarizing a long report you can spot-check",
                 "correct": False, "rationale": "Strong fit — the source is available for verification."},
                {"id": "b", "text": "Drafting a routine status update you will review",
                 "correct": False, "rationale": "Strong fit — a human reviews every word before it is sent."},
                {"id": "c", "text": "Computing a fee schedule where errors are invisible until they cost money",
                 "correct": True,
                 "rationale": "Precise calculation with hidden failure costs is the classic poor fit; use verified tools."},
                {"id": "d", "text": "Brainstorming an agenda for an internal meeting",
                 "correct": False, "rationale": "Low-stakes ideation is a strong fit for AI assistance."},
            ],
        },
        {
            "question_type": "multi_select",
            "prompt": "Which elements belong in a strong prompt? Select all that apply.",
            "objective_ref": 2,
            "options": [
                {"id": "a", "text": "Relevant background material pasted in", "correct": True,
                 "rationale": "Providing sources grounds the output and enables verification."},
                {"id": "b", "text": "One clear task instruction", "correct": True,
                 "rationale": "A single unambiguous task outperforms several tangled ones."},
                {"id": "c", "text": "The desired output format", "correct": True,
                 "rationale": "Stating format (memo, table, word count) removes a whole revision round."},
                {"id": "d", "text": "Deliberately vague wording so the model stays creative", "correct": False,
                 "rationale": "Vagueness produces generic output; creativity is better requested explicitly."},
            ],
        },
    ],
}


async def seed(author_email: str, reset: bool) -> None:
    conn = await asyncpg.connect(dsn=get_settings().database_url, statement_cache_size=0)
    try:
        author = await conn.fetchrow(
            "select id from auth.users where email = $1", author_email)
        if author is None:
            raise SystemExit(
                f"No auth user with email {author_email!r}. Create one in the "
                "Supabase dashboard (Authentication -> Users) first.")

        existing = await conn.fetchval(
            "select id from tenants where slug = $1", TENANT_SLUG)
        if existing and not reset:
            raise SystemExit(
                f"Tenant '{TENANT_SLUG}' already seeded. Re-run with --reset to wipe it.")
        if existing and reset:
            await conn.execute("delete from tenants where id = $1", existing)

        async with conn.transaction():
            tenant_id = await conn.fetchval(
                """insert into tenants (slug, name, branding)
                   values ($1, 'Demo Training Co', '{"primary_color": "#1D4ED8"}')
                   returning id""", TENANT_SLUG)

            await conn.execute(
                """insert into profiles (id, tenant_id, role, display_name)
                   values ($1, $2, 'owner', 'Demo Author')
                   on conflict (id) do update set tenant_id = $2, role = 'owner'""",
                author["id"], tenant_id)

            profile_id = await conn.fetchval(
                """insert into audience_profiles (tenant_id, name, attributes)
                   values ($1, 'AEC professionals', $2) returning id""",
                tenant_id, json.dumps(AEC_PROFILE))

            course_id = await conn.fetchval(
                """insert into courses (tenant_id, title, description, created_by)
                   values ($1, 'Practical AI for Professionals',
                           'A short, audience-adaptable introduction to working with AI.',
                           $2) returning id""", tenant_id, author["id"])

            version_id = await conn.fetchval(
                """insert into course_versions
                     (tenant_id, course_id, is_canonical, label, status,
                      learning_objectives)
                   values ($1, $2, true, 'Canonical', 'published', $3)
                   returning id""", tenant_id, course_id, json.dumps(OBJECTIVES))

            for m_pos, (m_title, lessons) in enumerate(COURSE, start=1):
                module_id = await conn.fetchval(
                    """insert into modules (tenant_id, version_id, title, position)
                       values ($1, $2, $3, $4) returning id""",
                    tenant_id, version_id, m_title, m_pos)
                for l_pos, (l_title, minutes, blocks) in enumerate(lessons, start=1):
                    lesson_id = await conn.fetchval(
                        """insert into lessons
                             (tenant_id, module_id, title, position, estimated_minutes)
                           values ($1, $2, $3, $4, $5) returning id""",
                        tenant_id, module_id, l_title, l_pos, minutes)
                    for b_pos, block in enumerate(blocks, start=1):
                        await conn.execute(
                            """insert into content_blocks
                                 (tenant_id, lesson_id, position, block_type,
                                  content, adaptable)
                               values ($1, $2, $3, $4, $5, $6)""",
                            tenant_id, lesson_id, b_pos, block["t"],
                            json.dumps(block["c"]), block.get("adaptable", True))

            quiz_id = await conn.fetchval(
                """insert into quizzes (tenant_id, version_id, title, pass_pct)
                   values ($1, $2, $3, $4) returning id""",
                tenant_id, version_id, QUIZ["title"], QUIZ["pass_pct"])
            for q_pos, q in enumerate(QUIZ["questions"], start=1):
                await conn.execute(
                    """insert into questions
                         (tenant_id, quiz_id, position, question_type, prompt,
                          options, objective_ref)
                       values ($1, $2, $3, $4, $5, $6, $7)""",
                    tenant_id, quiz_id, q_pos, q["question_type"], q["prompt"],
                    json.dumps(q["options"]), q["objective_ref"])
            # attach the quiz to the final lesson as a quiz_ref block
            await conn.execute(
                """insert into content_blocks
                     (tenant_id, lesson_id, position, block_type, content)
                   select $1, l.id, 99, 'quiz_ref', $2
                   from lessons l
                   join modules m on m.id = l.module_id
                   where m.version_id = $3
                   order by m.position desc, l.position desc limit 1""",
                tenant_id, json.dumps({"quiz_id": str(quiz_id)}), version_id)

        print("Seeded successfully.")
        print(f"  tenant_id           = {tenant_id}")
        print(f"  course_id           = {course_id}")
        print(f"  canonical_version   = {version_id}")
        print(f"  audience_profile_id = {profile_id}")
        print(f"  quiz_id             = {quiz_id}")
        print("\nKick off an adaptation job (replace <JWT> with the author's "
              "access token from the frontend or Supabase auth):")
        print(f"""
curl -X POST http://localhost:8000/adaptation/jobs \\
  -H "Authorization: Bearer <JWT>" -H "Content-Type: application/json" \\
  -d '{{"course_id": "{course_id}", "audience_profile_id": "{profile_id}", "label": "AEC v1"}}'
""")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--author-email", required=True,
                        help="email of an existing Supabase auth user")
    parser.add_argument("--reset", action="store_true",
                        help="delete the demo tenant and reseed")
    args = parser.parse_args()
    asyncio.run(seed(args.author_email, args.reset))
