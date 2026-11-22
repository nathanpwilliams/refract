"""End-to-end run_job wiring against a fake database and stubbed models.

No API, no Postgres: the db helpers are replaced with an in-memory store
that behaves the way the real ones do in the one respect the warranty
depends on — the deterministic checks read the TARGET VERSION BACK, so a
write that never happened cannot pass a check.

What this pins down is the orchestration: the status sequence, that only
touched blocks reach REWRITE, that VERIFY runs once per verifier, that a
reversion is actually written to the version, what lands in
block_revisions, and the publish-vs-halt decision with its evidence.

SQL text itself is not exercised here — only a live run proves that.
"""
import json

import pytest

from app import db
from app.config import Settings
from app.services import checks
from app.services import disposition as dispo
from app.services import llm
from app.services import pipeline
from app.services import prompts
from app.services import verifiers

TENANT = "tenant-1"
JOB_ID = "job-1"
CANON_VERSION = "canon-version"
TARGET_VERSION = "target-version"
PROFILE_ID = "profile-1"
LESSON_ID = "lesson-1"
TARGET_LESSON_ID = "target-lesson-1"

PROFILE_ATTRS = {
    "role": "managers",
    "ai_failure_modes": ["Approving a direct report's AI-drafted work unread"],
    "vocabulary": {"preferred": ["direct report"], "avoid": ["circle back"]},
}
OBJECTIVES = ["Apply verification practices before relying on AI output"]

CANONICAL_BLOCKS = [
    {"position": 1, "block_type": "heading", "adaptable": True,
     "content": {"level": 2, "text": "Verify before you rely"}},
    {"position": 2, "block_type": "text", "adaptable": True,
     "content": {"markdown": (
         "Before you rely on AI output, check the parts that carry risk: the "
         "facts, the judgment, and anything confidential. A fluent draft is "
         "not a checked draft, and the person who passes it on owns it.")}},
    {"position": 3, "block_type": "text", "adaptable": False,
     "content": {"markdown": "Hallucination: fluent output with no source."}},
    {"position": 4, "block_type": "knowledge_check", "adaptable": True,
     "content": {"question": "A summary cites a figure you do not recognize.",
                 "options": [
                     {"id": "a", "text": "Check the source", "correct": True,
                      "feedback": "Right."},
                     {"id": "b", "text": "Pass it on", "correct": False,
                      "feedback": "Confidence is not accuracy."}],
                 "style": "single"}},
]

# A clean, warranted rewrite of block 2 (position 2 -> canonical id "c2").
ADAPTED_C2 = {"markdown": (
    "Before you sign off on AI-assisted work from a direct report, check the "
    "parts that carry risk: the facts, the judgment, and anything "
    "confidential. A fluent status summary is not a checked one, and the "
    "person who passes it upward owns it.")}


# --------------------------------------------------------------- the fake db

class FakeDB:
    """Minimal in-memory stand-in for app.db's tenant helpers."""

    def __init__(self):
        self.statuses: list[str] = []
        self.revisions: list[dict] = []
        self.version_status: dict[str, dict] = {}
        self.job: dict = {}
        self.blocks: dict[str, list[dict]] = {
            CANON_VERSION: [
                {"lesson_id": LESSON_ID, "block_id": f"c{b['position']}",
                 "source_block_id": None, "position": b["position"],
                 "block_type": b["block_type"], "adaptable": b["adaptable"],
                 "content": json.dumps(b["content"])}
                for b in CANONICAL_BLOCKS
            ],
            TARGET_VERSION: [],
        }

    # -- the three helpers pipeline.py uses -----------------------------
    async def fetchrow_tenant(self, tenant_id, query, *args):
        assert tenant_id == TENANT
        if "from audience_profiles" in query:
            return {"name": "managers", "attributes": json.dumps(PROFILE_ATTRS)}
        if "learning_objectives" in query:
            return {"learning_objectives": json.dumps(OBJECTIVES)}
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetch_tenant(self, tenant_id, query, *args):
        version_id = args[0]
        if "from modules m join lessons l" in query:
            # the copied lesson carries its own id, as in the real schema
            return [{"lesson_id": LESSON_ID if version_id == CANON_VERSION
                                  else TARGET_LESSON_ID,
                     "title": "Verify before you rely"}]
        if "from content_blocks cb" in query:
            canonical = "cb.id as block_id" in query
            rows = self.blocks[version_id]
            return [
                {**r,
                 "lesson_id": r["lesson_id"],
                 "block_id": r["block_id"] if canonical else r["source_block_id"]}
                for r in rows
            ]
        raise AssertionError(f"unexpected fetch: {query}")

    async def execute_tenant(self, tenant_id, query, *args):
        assert tenant_id == TENANT
        if "set status = $2 where tenant_id = $1 and id = $3" in query \
                and "adaptation_jobs" in query:
            self.statuses.append(args[0])
        elif "set profile_snapshot" in query:
            self.job["profile_snapshot"] = json.loads(args[0])
        elif "set plan" in query:
            self.job["plan"] = json.loads(args[0])
        elif "update content_blocks set content" in query:
            content, lesson_id, source_block_id = args
            for row in self.blocks[TARGET_VERSION]:
                if (row["lesson_id"] == lesson_id
                        and row["source_block_id"] == source_block_id):
                    row["content"] = content
                    return
            raise AssertionError(f"no target block for {source_block_id}")
        elif "insert into block_revisions" in query:
            (job_id, block_id, original, generated, notes, review_status,
             reviewer, reason) = args
            self.revisions.append({
                "job_id": job_id, "block_id": block_id,
                "original": json.loads(original), "generated": json.loads(generated),
                "verifier_notes": json.loads(notes), "review_status": review_status,
                "reviewer": reviewer, "disposition_reason": reason})
        elif "update course_versions" in query:
            self.version_status[args[0]] = {
                "status": "published" if "published" in query else "halted"}
        elif "update adaptation_jobs" in query and "evidence" in query:
            self.job["evidence"] = json.loads(args[0])
            self.job["status"] = "done" if "'done'" in query else "halted"
            self.job["error"] = None if "'done'" in query else args[1]
        else:
            raise AssertionError(f"unexpected execute: {query}")

    # -- stands in for the tree copy (which uses a raw connection) -------
    def copy_tree(self) -> dict[str, str]:
        self.blocks[TARGET_VERSION] = [
            {"lesson_id": TARGET_LESSON_ID, "block_id": f"t{r['position']}",
             "source_block_id": r["block_id"], "position": r["position"],
             "block_type": r["block_type"], "adaptable": r["adaptable"],
             "content": r["content"]}
            for r in self.blocks[CANON_VERSION]
        ]
        return {LESSON_ID: TARGET_LESSON_ID}

    def target_content(self, position: int) -> dict:
        row = next(r for r in self.blocks[TARGET_VERSION]
                   if r["position"] == position)
        return json.loads(row["content"])


# ------------------------------------------------------------- model stubbing

class Models:
    """Routes a stubbed complete_json by which system prompt it was given."""

    def __init__(self, plan, rewrite, verify, dispositions):
        self.plan, self.rewrite = plan, rewrite
        self.verify, self.dispositions = verify, dispositions
        self.calls: list[dict] = []

    async def complete_json(self, system, user, temperature, model=None):
        self.calls.append({"system": system, "user": user, "model": model,
                           "temperature": temperature})
        if system == prompts.PLAN_SYSTEM:
            return self.plan
        if system == prompts.REWRITE_SYSTEM:
            return self.rewrite
        if system == prompts.VERIFY_SYSTEM:
            # one entry per verifier, in call order
            index = sum(1 for c in self.calls if c["system"] == prompts.VERIFY_SYSTEM) - 1
            return self.verify[min(index, len(self.verify) - 1)]
        if system == prompts.DISPOSITION_SYSTEM:
            return self.dispositions
        raise AssertionError("unrecognized system prompt")

    def stage(self, system) -> list[dict]:
        return [c for c in self.calls if c["system"] == system]


SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    database_url="postgresql://localhost/test",
    anthropic_api_key="sk-ant-test",
    adaptation_model="model-main",
    verify_model_a="model-a",
    verify_model_b="model-b",
)

PLAN_TOUCHING_2_AND_4 = {
    "audience_summary": "Managers reviewing other people's AI-assisted work.",
    "lessons": [{"lesson_id": LESSON_ID, "touch_list": [
        {"block_position": 2, "reason": "generic risk framing",
         "adaptation_instruction": "Transpose onto reviewing a direct report's work."},
        {"block_position": 4, "reason": "stem is audience-neutral",
         "adaptation_instruction": "Set the stem in a status summary."},
    ]}],
}

REWRITE_C2_ONLY_C4_UNCHANGED = {"blocks": [
    {"block_id": "c2", "unchanged": False, "reason": None, "content": ADAPTED_C2},
    {"block_id": "c4", "unchanged": True,
     "reason": "rewording the stem would change which option is correct"},
]}

ACCEPT_BOTH = {"dispositions": [
    {"block_id": "c2", "decision": "accept",
     "reason": "names the direct-report sign-off this role actually carries"},
    {"block_id": "c4", "decision": "accept", "reason": "unchanged"},
]}


@pytest.fixture
def run(monkeypatch):
    """Returns a callable that drives run_job and hands back the fake db."""
    def _run(models: Models, fake: FakeDB | None = None):
        import asyncio
        fake = fake or FakeDB()
        for module in (pipeline, verifiers, dispo):
            monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
        monkeypatch.setattr(llm, "complete_json", models.complete_json)
        monkeypatch.setattr(db, "fetchrow_tenant", fake.fetchrow_tenant)
        monkeypatch.setattr(db, "fetch_tenant", fake.fetch_tenant)
        monkeypatch.setattr(db, "execute_tenant", fake.execute_tenant)

        async def copy_tree(tenant_id, source, target):
            return fake.copy_tree()
        monkeypatch.setattr(pipeline, "_copy_version_tree", copy_tree)

        asyncio.run(pipeline.run_job({
            "id": JOB_ID, "tenant_id": TENANT,
            "source_version_id": CANON_VERSION,
            "target_version_id": TARGET_VERSION,
            "audience_profile_id": PROFILE_ID,
        }))
        return fake
    return _run


# ------------------------------------------------------------- the clean run

def test_a_clean_sparse_run_auto_publishes(run):
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    fake = run(models)

    assert fake.statuses == ["planning", "rewriting", "checking", "verifying",
                             "disposing"]
    assert fake.version_status[TARGET_VERSION]["status"] == "published"
    assert fake.job["status"] == "done"
    assert fake.job["evidence"]["outcome"] == "published"
    assert fake.job["evidence"]["checks"]["failed"] == 0


def test_only_touched_blocks_reach_the_rewriter(run):
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    run(models)

    rewrite_calls = models.stage(prompts.REWRITE_SYSTEM)
    assert len(rewrite_calls) == 1
    body = rewrite_calls[0]["user"]
    assert '"block_id": "c2"' in body and '"block_id": "c4"' in body
    # the locked block and the untouched heading are never shown to a model
    assert "c3" not in body and "Hallucination" not in body
    assert "c1" not in body


def test_the_rewriter_is_handed_the_computed_word_budget(run):
    """A percentage is something a model estimates; a range is something it
    can obey. The first live run overran +/-30% on five of nine blocks."""
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    run(models)

    body = models.stage(prompts.REWRITE_SYSTEM)[0]["user"]
    payload = json.loads(
        body.split("<lesson title=", 1)[1].split(">", 1)[1]
            .rsplit("</lesson>", 1)[0])
    by_block = {b["block_id"]: b for b in payload}

    canonical_c2 = next(b["content"] for b in CANONICAL_BLOCKS if b["position"] == 2)
    words = checks.word_count(canonical_c2)
    low, high = checks.length_bounds(words)
    assert by_block["c2"]["canonical_word_count"] == words
    assert by_block["c2"]["allowed_word_count"] == {"min": low, "max": high}
    # and the prompt tells it the range is enforced by computation
    assert "allowed_word_count" in models.stage(prompts.REWRITE_SYSTEM)[0]["system"]


def test_untouched_and_locked_blocks_are_byte_identical_after_the_run(run):
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    fake = run(models)

    for position in (1, 3, 4):      # 4 was touched but returned unchanged
        assert (checks.canonical_form(fake.target_content(position))
                == checks.canonical_form(
                    next(b["content"] for b in CANONICAL_BLOCKS
                         if b["position"] == position)))
    assert fake.target_content(2) == ADAPTED_C2


def test_verify_runs_once_per_verifier_with_both_models(run):
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    run(models)

    verify_calls = models.stage(prompts.VERIFY_SYSTEM)
    assert [c["model"] for c in verify_calls] == ["model-a", "model-b"]
    assert all(c["temperature"] == 0.0 for c in verify_calls)


def test_evidence_records_the_whole_chain(run):
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    fake = run(models)
    evidence = fake.job["evidence"]

    assert evidence["pipeline_version"] == "2"
    assert evidence["touch_rate"] == {
        "touched": 2, "total": 4, "rate": 0.5,
        "by_lesson": [{"lesson_id": LESSON_ID, "title": "Verify before you rely",
                       "touched": 2, "total": 4}]}
    assert evidence["models"] == {
        "plan": "model-main", "rewrite": "model-main", "disposition": "model-main",
        "verify_a": "anthropic:model-a", "verify_b": "anthropic:model-b"}
    assert evidence["prompt_versions"] == prompts.PROMPT_VERSIONS
    assert evidence["dispositions"]["accepted"] == 2
    assert evidence["dispositions"]["unchanged_by_rewrite"] == 1
    assert evidence["canonical"]["block_count"] == 4
    assert len(evidence["canonical"]["block_sha256"]) == 64
    assert evidence["plan_warnings"] == []
    # the profile is frozen at claim time, not referenced live
    assert fake.job["profile_snapshot"]["role"] == "managers"


def test_revisions_record_the_machine_disposition(run):
    models = Models(PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
                    [{"findings": []}, {"findings": []}], ACCEPT_BOTH)
    fake = run(models)

    assert {r["block_id"] for r in fake.revisions} == {"c2", "c4"}
    for revision in fake.revisions:
        assert revision["review_status"] == "auto_accepted"
        assert revision["reviewer"] == "pipeline_v2"
        assert revision["disposition_reason"]
        # never 'pending': the publish endpoint's human gate stays satisfied
        assert revision["review_status"] != "pending"
        assert revision["generated"]["adaptation_instruction"]


# ------------------------------------------------------------- the halt paths

def test_a_blocker_from_one_verifier_halts_the_version(run):
    models = Models(
        PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
        [{"findings": []},                      # verify_a: clean
         {"findings": [{"block_id": "c2", "issue": "fact_drift",
                        "severity": "blocker",
                        "detail": "introduced a sign-off claim not in the canonical"}]}],
        ACCEPT_BOTH)
    fake = run(models)

    assert fake.version_status[TARGET_VERSION]["status"] == "halted"
    assert fake.job["status"] == "halted"
    assert "blocker finding" in fake.job["error"]
    assert "verify_b/fact_drift" in fake.job["error"]
    assert fake.job["evidence"]["findings"]["blockers"] == 1


def test_a_halt_disposition_halts_the_version(run):
    models = Models(
        PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
        [{"findings": []}, {"findings": []}],
        {"dispositions": [
            {"block_id": "c2", "decision": "halt",
             "reason": "the canonical understates the manager's accountability"},
            {"block_id": "c4", "decision": "accept", "reason": "unchanged"}]})
    fake = run(models)

    assert fake.job["status"] == "halted"
    assert "halt disposition" in fake.job["error"]
    # a halted version must not carry the change nobody vouched for
    assert fake.target_content(2) == next(
        b["content"] for b in CANONICAL_BLOCKS if b["position"] == 2)


def test_a_rewrite_that_breaks_the_answer_key_halts_on_the_check(run):
    sabotaged = json.loads(json.dumps(
        next(b["content"] for b in CANONICAL_BLOCKS if b["position"] == 4)))
    sabotaged["options"][0]["correct"] = False
    sabotaged["options"][1]["correct"] = True

    models = Models(
        PLAN_TOUCHING_2_AND_4,
        {"blocks": [{"block_id": "c4", "unchanged": False, "content": sabotaged}]},
        [{"findings": []}, {"findings": []}],
        ACCEPT_BOTH)
    fake = run(models)

    assert fake.version_status[TARGET_VERSION]["status"] == "halted"
    assert "answer_key_integrity" in fake.job["error"]
    assert fake.job["evidence"]["checks"]["failed"] >= 1
    # the deterministic failure is attached to the block's revision too
    c4 = next(r for r in fake.revisions if r["block_id"] == "c4")
    assert any(n["source"] == "checks" for n in c4["verifier_notes"])


def test_plan_selecting_the_locked_block_halts_without_rewriting_it(run):
    plan = {"lessons": [{"lesson_id": LESSON_ID, "touch_list": [
        {"block_position": 3, "reason": "wants plainer definitions",
         "adaptation_instruction": "Reword for managers."}]}]}
    models = Models(plan, {"blocks": []}, [{"findings": []}], {"dispositions": []})
    fake = run(models)

    assert models.stage(prompts.REWRITE_SYSTEM) == []    # never sent anywhere
    assert fake.version_status[TARGET_VERSION]["status"] == "halted"
    assert "locked_integrity" in fake.job["error"]
    assert any("LOCKED" in w for w in fake.job["evidence"]["plan_warnings"])
    assert fake.target_content(3) == next(
        b["content"] for b in CANONICAL_BLOCKS if b["position"] == 3)


# --------------------------------------------------------- reversion behavior

def test_a_reverted_block_is_written_back_to_canonical(run):
    models = Models(
        PLAN_TOUCHING_2_AND_4, REWRITE_C2_ONLY_C4_UNCHANGED,
        [{"findings": [{"block_id": "c2", "issue": "unwarranted_change",
                        "severity": "advisory", "detail": "wording only"}]},
         {"findings": []}],
        {"dispositions": [
            {"block_id": "c2", "decision": "revert_to_canonical",
             "reason": "near-zero semantic delta"},
            {"block_id": "c4", "decision": "accept", "reason": "unchanged"}]})
    fake = run(models)

    # advisory findings do not halt; the reversion is what ships
    assert fake.version_status[TARGET_VERSION]["status"] == "published"
    assert fake.target_content(2) == next(
        b["content"] for b in CANONICAL_BLOCKS if b["position"] == 2)
    c2 = next(r for r in fake.revisions if r["block_id"] == "c2")
    assert c2["review_status"] == "auto_reverted"
    assert fake.job["evidence"]["dispositions"]["reverted"] == 1


def test_a_dropped_rewrite_entry_leaves_the_canonical_in_place(run):
    models = Models(PLAN_TOUCHING_2_AND_4, {"blocks": []},
                    [{"findings": []}, {"findings": []}], {"dispositions": []})
    fake = run(models)

    assert fake.version_status[TARGET_VERSION]["status"] == "published"
    assert fake.target_content(2) == next(
        b["content"] for b in CANONICAL_BLOCKS if b["position"] == 2)
    assert fake.job["evidence"]["dispositions"]["unchanged_by_rewrite"] == 2
    assert models.stage(prompts.DISPOSITION_SYSTEM) == []   # nothing to judge


def test_an_empty_touch_list_publishes_a_verbatim_copy(run):
    plan = {"lessons": [{"lesson_id": LESSON_ID, "touch_list": []}]}
    models = Models(plan, {"blocks": []}, [], {"dispositions": []})
    fake = run(models)

    assert fake.job["evidence"]["touch_rate"]["touched"] == 0
    assert fake.version_status[TARGET_VERSION]["status"] == "published"
    assert models.stage(prompts.REWRITE_SYSTEM) == []
    assert models.stage(prompts.VERIFY_SYSTEM) == []
    assert fake.revisions == []
