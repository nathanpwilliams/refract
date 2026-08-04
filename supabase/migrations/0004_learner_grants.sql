-- =============================================================
-- Migration 0004 — privileges for the authenticated role
-- Auto-expose is OFF (deliberate), so API access is granted
-- explicitly. RLS (0003) still governs which rows; these grants
-- only unlock the tables the player touches. anon gets nothing.
-- =============================================================

grant usage on schema public to authenticated;

grant select on
  profiles, courses, course_versions, modules, lessons,
  content_blocks, quizzes, questions
to authenticated;

grant select, insert, update on enrollments, lesson_progress to authenticated;
grant select, insert on quiz_attempts to authenticated;