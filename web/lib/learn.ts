import { supabase } from "./supabase";

/* ---------- types ---------- */

export interface BlockRow {
  id: string;
  lesson_id: string;
  position: number;
  block_type:
    | "heading" | "text" | "image" | "video_embed" | "callout"
    | "code" | "table" | "quiz_ref" | "knowledge_check" | "scenario";
  content: Record<string, unknown>;
}

export interface LessonRow {
  id: string;
  module_id: string;
  title: string;
  position: number;
  estimated_minutes: number | null;
}

export interface ModuleRow {
  id: string;
  title: string;
  position: number;
  lessons: LessonRow[];
}

export interface VersionInfo {
  id: string;
  label: string;
  status: string;
  learning_objectives: string[];
  course: { title: string; description: string | null };
}

export interface QuizRow {
  id: string;
  title: string;
  pass_pct: number;
  questions: {
    id: string;
    position: number;
    question_type: "multiple_choice" | "multi_select" | "true_false";
    prompt: string;
    options: { id: string; text: string; correct: boolean; rationale: string }[];
  }[];
}

export type ProgressMap = Record<string, "not_started" | "in_progress" | "complete">;

/* ---------- reads ---------- */

export async function fetchVersion(versionId: string): Promise<VersionInfo> {
  const { data, error } = await supabase
    .from("course_versions")
    .select("id, label, status, learning_objectives, courses(title, description)")
    .eq("id", versionId)
    .single();
  if (error) throw error;
  const course = Array.isArray(data.courses) ? data.courses[0] : data.courses;
  return {
    id: data.id,
    label: data.label,
    status: data.status,
    learning_objectives: (data.learning_objectives ?? []) as string[],
    course: {
      title: course?.title ?? "Untitled course",
      description: course?.description ?? null,
    },
  };
}

export async function fetchTree(versionId: string): Promise<ModuleRow[]> {
  const { data: modules, error: mErr } = await supabase
    .from("modules")
    .select("id, title, position")
    .eq("version_id", versionId)
    .order("position");
  if (mErr) throw mErr;

  const { data: lessons, error: lErr } = await supabase
    .from("lessons")
    .select("id, module_id, title, position, estimated_minutes")
    .in("module_id", modules.map((m) => m.id))
    .order("position");
  if (lErr) throw lErr;

  return modules.map((m) => ({
    ...m,
    lessons: lessons.filter((l) => l.module_id === m.id),
  }));
}

export async function fetchBlocks(lessonId: string): Promise<BlockRow[]> {
  const { data, error } = await supabase
    .from("content_blocks")
    .select("id, lesson_id, position, block_type, content")
    .eq("lesson_id", lessonId)
    .order("position");
  if (error) throw error;
  return data as BlockRow[];
}

export async function fetchQuiz(quizId: string): Promise<QuizRow> {
  const { data: quiz, error: qErr } = await supabase
    .from("quizzes")
    .select("id, title, pass_pct")
    .eq("id", quizId)
    .single();
  if (qErr) throw qErr;
  const { data: questions, error: qsErr } = await supabase
    .from("questions")
    .select("id, position, question_type, prompt, options")
    .eq("quiz_id", quizId)
    .order("position");
  if (qsErr) throw qsErr;
  return { ...quiz, questions: questions as QuizRow["questions"] };
}

/* ---------- enrollment & progress ---------- */

export async function ensureEnrollment(versionId: string): Promise<string> {
  const { data: session } = await supabase.auth.getSession();
  const userId = session.session?.user.id;
  if (!userId) throw new Error("Not signed in");

  const { data: existing } = await supabase
    .from("enrollments")
    .select("id")
    .eq("version_id", versionId)
    .eq("user_id", userId)
    .maybeSingle();
  if (existing) return existing.id;

  const { data: profile, error: pErr } = await supabase
    .from("profiles")
    .select("tenant_id")
    .eq("id", userId)
    .single();
  if (pErr) throw pErr;

  const { data: created, error: cErr } = await supabase
    .from("enrollments")
    .insert({ version_id: versionId, user_id: userId, tenant_id: profile.tenant_id })
    .select("id")
    .single();
  if (cErr) throw cErr;
  return created.id;
}

export async function fetchProgress(enrollmentId: string): Promise<ProgressMap> {
  const { data, error } = await supabase
    .from("lesson_progress")
    .select("lesson_id, status")
    .eq("enrollment_id", enrollmentId);
  if (error) throw error;
  return Object.fromEntries(data.map((r) => [r.lesson_id, r.status])) as ProgressMap;
}

export async function setLessonStatus(
  enrollmentId: string,
  lessonId: string,
  tenantId: string,
  status: "in_progress" | "complete",
): Promise<void> {
  const { error } = await supabase.from("lesson_progress").upsert(
    {
      enrollment_id: enrollmentId,
      lesson_id: lessonId,
      tenant_id: tenantId,
      status,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "enrollment_id,lesson_id" },
  );
  if (error) throw error;
}

export async function recordQuizAttempt(args: {
  enrollmentId: string;
  quizId: string;
  tenantId: string;
  scorePct: number;
  passed: boolean;
  answers: Record<string, string[]>;
}): Promise<void> {
  const { error } = await supabase.from("quiz_attempts").insert({
    enrollment_id: args.enrollmentId,
    quiz_id: args.quizId,
    tenant_id: args.tenantId,
    score_pct: args.scorePct,
    passed: args.passed,
    answers: args.answers,
  });
  if (error) throw error;
}

export async function markCompletedIfDone(
  enrollmentId: string,
  allLessonIds: string[],
): Promise<boolean> {
  const progress = await fetchProgress(enrollmentId);
  const done = allLessonIds.every((id) => progress[id] === "complete");
  if (done) {
    await supabase
      .from("enrollments")
      .update({ completed_at: new Date().toISOString() })
      .eq("id", enrollmentId);
  }
  return done;
}

export async function myTenantId(): Promise<string> {
  const { data: session } = await supabase.auth.getSession();
  const userId = session.session?.user.id;
  if (!userId) throw new Error("Not signed in");
  const { data, error } = await supabase
    .from("profiles").select("tenant_id").eq("id", userId).single();
  if (error) throw error;
  return data.tenant_id;
}
