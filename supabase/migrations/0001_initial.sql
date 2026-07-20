-- =============================================================
-- AI Training Platform — Course Engine Schema (Postgres/Supabase)
-- Design principles:
--   1. Courses are DATA. Delivery (portal, SCORM) renders this data.
--   2. course_versions is where audience adaptation lives.
--   3. Every table carries tenant_id for row-level security.
--   4. Accessibility fields are first-class, not bolted on.
-- =============================================================

-- ---------- Tenancy & identity ----------

create table tenants (
  id            uuid primary key default gen_random_uuid(),
  slug          text unique not null,          -- subdomain: acme.yourplatform.com
  name          text not null,
  branding      jsonb not null default '{}',   -- {logo_url, primary_color, accent_color, favicon_url}
  settings      jsonb not null default '{}',   -- {cert_signature_name, support_email, ...}
  created_at    timestamptz not null default now()
);

-- Supabase manages auth.users; this profile table joins users to tenants.
create table profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  tenant_id     uuid not null references tenants(id) on delete cascade,
  role          text not null default 'learner'
                check (role in ('owner','author','learner')),
  display_name  text,
  created_at    timestamptz not null default now()
);

-- ---------- Audience profiles (input to adaptation) ----------

create table audience_profiles (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  name          text not null,                 -- "AEC professionals"
  attributes    jsonb not null,
  -- attributes shape:
  -- {
  --   "industry":        "Architecture, Engineering & Construction",
  --   "roles":           ["architects","project managers"],
  --   "prior_knowledge": "novice|intermediate|advanced",
  --   "example_domains": ["BIM workflows","RFI triage","site photo analysis"],
  --   "vocabulary":      {"prefer": ["design phase","submittal"], "avoid": ["sprint","backlog"]},
  --   "tone":            "professional, plain language",
  --   "compliance_notes":"Section 508; no vendor endorsements"
  -- }
  created_at    timestamptz not null default now()
);

-- ---------- Courses & versions ----------

create table courses (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  title         text not null,
  description   text,
  created_by    uuid references profiles(id),
  created_at    timestamptz not null default now()
);

create table course_versions (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references tenants(id) on delete cascade,
  course_id           uuid not null references courses(id) on delete cascade,
  audience_profile_id uuid references audience_profiles(id),  -- null => canonical
  is_canonical        boolean not null default false,
  label               text not null,           -- "Canonical", "AEC v1"
  status              text not null default 'draft'
                      check (status in ('draft','generating','generated','in_review','published','archived')),
  version_number      int  not null default 1,
  learning_objectives jsonb not null default '[]',  -- ["Identify 3 AI use cases in AEC", ...]
  published_at        timestamptz,
  created_at          timestamptz not null default now(),
  unique (course_id, label)
);
create unique index one_canonical_per_course
  on course_versions (course_id) where is_canonical;

create table modules (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  version_id    uuid not null references course_versions(id) on delete cascade,
  title         text not null,
  position      int  not null,
  unique (version_id, position)
);

create table lessons (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references tenants(id) on delete cascade,
  module_id         uuid not null references modules(id) on delete cascade,
  title             text not null,
  position          int  not null,
  estimated_minutes int,
  unique (module_id, position)
);

-- ---------- Content blocks (the atomic unit everything renders from) ----------

create table content_blocks (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  lesson_id     uuid not null references lessons(id) on delete cascade,
  position      int  not null,
  block_type    text not null check (block_type in
                ('heading','text','image','video_embed','callout','code','table','quiz_ref')),
  content       jsonb not null,
  -- content shapes by block_type:
  --   heading:     {"level": 2, "text": "..."}
  --   text:        {"markdown": "..."}
  --   image:       {"storage_path": "...", "alt_text": "...", "caption": "..."}   -- alt_text REQUIRED by app logic
  --   video_embed: {"url": "...", "title": "...", "transcript_md": "...", "captions": true}
  --   callout:     {"variant": "tip|warning|key_idea", "markdown": "..."}
  --   code:        {"language": "...", "source": "...", "caption": "..."}
  --   table:       {"headers": [...], "rows": [[...]], "caption": "..."}
  --   quiz_ref:    {"quiz_id": "<uuid>"}
  adaptable     boolean not null default true,   -- false = never rewritten by the pipeline
  source_block_id uuid references content_blocks(id),  -- lineage: which canonical block this was adapted from
  unique (lesson_id, position)
);

-- ---------- Quizzes ----------

create table quizzes (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  version_id    uuid not null references course_versions(id) on delete cascade,
  title         text not null,
  pass_pct      int  not null default 80,
  max_attempts  int,                             -- null = unlimited
  created_at    timestamptz not null default now()
);

create table questions (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  quiz_id       uuid not null references quizzes(id) on delete cascade,
  position      int  not null,
  question_type text not null check (question_type in ('multiple_choice','multi_select','true_false')),
  prompt        text not null,
  options       jsonb not null,   -- [{"id":"a","text":"...","correct":true,"rationale":"..."}]
  objective_ref int,              -- index into course_versions.learning_objectives
  unique (quiz_id, position)
);

-- ---------- Enrollment, progress, certificates ----------

create table enrollments (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants(id) on delete cascade,
  version_id    uuid not null references course_versions(id) on delete cascade,
  user_id       uuid not null references profiles(id) on delete cascade,
  enrolled_at   timestamptz not null default now(),
  completed_at  timestamptz,
  unique (version_id, user_id)
);

create table lesson_progress (
  enrollment_id uuid not null references enrollments(id) on delete cascade,
  lesson_id     uuid not null references lessons(id) on delete cascade,
  tenant_id     uuid not null,
  status        text not null default 'not_started'
                check (status in ('not_started','in_progress','complete')),
  updated_at    timestamptz not null default now(),
  primary key (enrollment_id, lesson_id)
);

create table quiz_attempts (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null,
  enrollment_id uuid not null references enrollments(id) on delete cascade,
  quiz_id       uuid not null references quizzes(id) on delete cascade,
  score_pct     numeric(5,2) not null,
  passed        boolean not null,
  answers       jsonb not null,
  attempted_at  timestamptz not null default now()
);

create table certificates (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null,
  enrollment_id uuid not null references enrollments(id) on delete cascade,
  serial        text unique not null,            -- printed on cert; verifiable
  issued_at     timestamptz not null default now()
);

-- ---------- Adaptation pipeline bookkeeping ----------

create table adaptation_jobs (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references tenants(id) on delete cascade,
  source_version_id   uuid not null references course_versions(id),
  target_version_id   uuid not null references course_versions(id),
  audience_profile_id uuid not null references audience_profiles(id),
  status              text not null default 'queued'
                      check (status in ('queued','planning','rewriting','verifying','awaiting_review','done','failed')),
  plan                jsonb,          -- output of the PLAN prompt (per-lesson strategy)
  error               text,
  created_at          timestamptz not null default now(),
  finished_at         timestamptz
);

create table block_revisions (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null,
  job_id        uuid not null references adaptation_jobs(id) on delete cascade,
  block_id      uuid not null references content_blocks(id) on delete cascade,
  original      jsonb not null,      -- canonical content at time of adaptation
  generated     jsonb not null,      -- model output
  verifier_notes jsonb,              -- output of VERIFY prompt
  review_status text not null default 'pending'
                check (review_status in ('pending','accepted','edited','rejected')),
  reviewed_by   uuid references profiles(id),
  reviewed_at   timestamptz
);

-- ---------- Row-level security (pattern; repeat for every table) ----------

alter table courses enable row level security;

create policy tenant_isolation_select on courses
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
  );

create policy author_write on courses
  for all using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and (select role from profiles where id = auth.uid()) in ('owner','author')
  );

-- Learners additionally need published-only visibility on course content:
-- add "and status = 'published'" style predicates on course_versions
-- select policies for role = 'learner'.

-- ---------- Indexes ----------

create index idx_blocks_lesson    on content_blocks (lesson_id, position);
create index idx_lessons_module   on lessons (module_id, position);
create index idx_modules_version  on modules (version_id, position);
create index idx_versions_course  on course_versions (course_id);
create index idx_progress_enroll  on lesson_progress (enrollment_id);
