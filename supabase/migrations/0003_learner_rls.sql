-- =============================================================
-- Migration 0003 — learner access policies (Phase 1: course player)
-- Auto-RLS enabled RLS on every table with no policies; this migration
-- grants the minimum the player needs:
--   * read own profile (tenant + role resolution)
--   * read published course content within own tenant
--     (authors/owners may also read unpublished, for preview)
--   * create/read own enrollment; write own lesson progress + attempts
-- The FastAPI service is unaffected (service role bypasses RLS).
-- =============================================================

-- ---------- profiles: read own row ----------
create policy profiles_select_own on profiles
  for select using (id = auth.uid());

-- Convenience predicates used repeatedly below (inlined as subqueries):
--   my tenant:  (select tenant_id from profiles where id = auth.uid())
--   my role:    (select role      from profiles where id = auth.uid())

-- ---------- published content, tenant-scoped ----------
create policy versions_read on course_versions
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and (
      status = 'published'
      or (select role from profiles where id = auth.uid()) in ('owner','author')
    )
  );

create policy courses_read on courses
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
  );

create policy modules_read on modules
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and exists (
      select 1 from course_versions v
      where v.id = modules.version_id
        and (v.status = 'published'
             or (select role from profiles where id = auth.uid()) in ('owner','author'))
    )
  );

create policy lessons_read on lessons
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and exists (
      select 1 from modules m
      join course_versions v on v.id = m.version_id
      where m.id = lessons.module_id
        and (v.status = 'published'
             or (select role from profiles where id = auth.uid()) in ('owner','author'))
    )
  );

create policy blocks_read on content_blocks
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and exists (
      select 1 from lessons l
      join modules m on m.id = l.module_id
      join course_versions v on v.id = m.version_id
      where l.id = content_blocks.lesson_id
        and (v.status = 'published'
             or (select role from profiles where id = auth.uid()) in ('owner','author'))
    )
  );

create policy quizzes_read on quizzes
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and exists (
      select 1 from course_versions v
      where v.id = quizzes.version_id
        and (v.status = 'published'
             or (select role from profiles where id = auth.uid()) in ('owner','author'))
    )
  );

create policy questions_read on questions
  for select using (
    tenant_id = (select tenant_id from profiles where id = auth.uid())
    and exists (
      select 1 from quizzes q
      join course_versions v on v.id = q.version_id
      where q.id = questions.quiz_id
        and (v.status = 'published'
             or (select role from profiles where id = auth.uid()) in ('owner','author'))
    )
  );

-- ---------- enrollments: own rows only ----------
create policy enrollments_select_own on enrollments
  for select using (user_id = auth.uid());

create policy enrollments_insert_own on enrollments
  for insert with check (
    user_id = auth.uid()
    and tenant_id = (select tenant_id from profiles where id = auth.uid())
  );

create policy enrollments_update_own on enrollments
  for update using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ---------- lesson_progress: via owned enrollment ----------
create policy progress_select_own on lesson_progress
  for select using (
    exists (select 1 from enrollments e
            where e.id = lesson_progress.enrollment_id and e.user_id = auth.uid())
  );

create policy progress_insert_own on lesson_progress
  for insert with check (
    exists (select 1 from enrollments e
            where e.id = lesson_progress.enrollment_id and e.user_id = auth.uid())
  );

create policy progress_update_own on lesson_progress
  for update using (
    exists (select 1 from enrollments e
            where e.id = lesson_progress.enrollment_id and e.user_id = auth.uid())
  ) with check (
    exists (select 1 from enrollments e
            where e.id = lesson_progress.enrollment_id and e.user_id = auth.uid())
  );

-- ---------- quiz_attempts: via owned enrollment ----------
create policy attempts_select_own on quiz_attempts
  for select using (
    exists (select 1 from enrollments e
            where e.id = quiz_attempts.enrollment_id and e.user_id = auth.uid())
  );

create policy attempts_insert_own on quiz_attempts
  for insert with check (
    exists (select 1 from enrollments e
            where e.id = quiz_attempts.enrollment_id and e.user_id = auth.uid())
  );
