"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import {
  ensureEnrollment, fetchBlocks, fetchQuiz, fetchTree, markCompletedIfDone,
  myTenantId, recordQuizAttempt, setLessonStatus,
  type BlockRow, type QuizRow,
} from "@/lib/learn";
import { Block } from "@/components/blocks/Block";
import { QuizRunner } from "@/components/QuizRunner";

export default function LessonPage() {
  const { versionId, lessonId } = useParams<{ versionId: string; lessonId: string }>();
  const router = useRouter();

  const [blocks, setBlocks] = useState<BlockRow[] | null>(null);
  const [quiz, setQuiz] = useState<QuizRow | null>(null);
  const [lessonTitle, setLessonTitle] = useState("");
  const [nav, setNav] = useState<{ prev: string | null; next: string | null; allIds: string[] }>({ prev: null, next: null, allIds: [] });
  const [enrollmentId, setEnrollmentId] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [bs, tree, eid, tid] = await Promise.all([
        fetchBlocks(lessonId),
        fetchTree(versionId),
        ensureEnrollment(versionId),
        myTenantId(),
      ]);
      setBlocks(bs);
      setEnrollmentId(eid);
      setTenantId(tid);

      const flat = tree.flatMap((m) => m.lessons);
      const idx = flat.findIndex((l) => l.id === lessonId);
      setLessonTitle(flat[idx]?.title ?? "");
      setNav({
        prev: idx > 0 ? flat[idx - 1].id : null,
        next: idx >= 0 && idx < flat.length - 1 ? flat[idx + 1].id : null,
        allIds: flat.map((l) => l.id),
      });

      const quizRef = bs.find((b) => b.block_type === "quiz_ref");
      if (quizRef) setQuiz(await fetchQuiz(String(quizRef.content.quiz_id)));

      await setLessonStatus(eid, lessonId, tid, "in_progress");
    } catch (e) {
      const msg = (e as { message?: string })?.message;
      setError(msg ?? "Failed to load lesson");
    }
  }, [lessonId, versionId]);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
      else void load();
    });
  }, [load, router]);

  async function markComplete() {
    if (!enrollmentId || !tenantId) return;
    await setLessonStatus(enrollmentId, lessonId, tenantId, "complete");
    setComplete(true);
    await markCompletedIfDone(enrollmentId, nav.allIds);
  }

  async function goNext() {
    await markComplete();
    if (nav.next) router.push(`/learn/${versionId}/lesson/${nav.next}`);
  }

  if (error)
    return <main className="mx-auto max-w-2xl px-6 py-16 text-sm text-[var(--reject)]">{error}</main>;
  if (!blocks)
    return <main className="mx-auto max-w-2xl px-6 py-16 text-sm text-[var(--muted)]">Loading lesson…</main>;

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <nav className="mb-6 text-sm">
        <Link href={`/learn/${versionId}`} className="text-[var(--muted)] underline underline-offset-4">
          ← Course overview
        </Link>
      </nav>

      <h1 className="text-2xl font-semibold tracking-tight">{lessonTitle}</h1>

      <div className="mt-6 flex flex-col gap-5">
        {blocks.map((b) => <Block key={b.id} block={b} />)}
      </div>

      {quiz && enrollmentId && tenantId && (
        <div className="mt-8">
          <QuizRunner
            quiz={quiz}
            onComplete={async (scorePct, passed, answers) => {
              await recordQuizAttempt({
                enrollmentId, quizId: quiz.id, tenantId, scorePct, passed, answers,
              });
              if (passed) await markComplete();
            }}
          />
        </div>
      )}

      <footer className="mt-10 flex items-center justify-between border-t border-[var(--line)] pt-6">
        <div>
          {nav.prev ? (
            <Link href={`/learn/${versionId}/lesson/${nav.prev}`} className="text-sm underline underline-offset-4">
              ← Previous lesson
            </Link>
          ) : <span />}
        </div>

        <div className="flex items-center gap-3">
          {complete && (
            <span
              className="rounded-md px-3 py-1.5 text-sm font-semibold"
              style={{ backgroundColor: "var(--accept-bg)", color: "var(--accept)" }}
            >
              Lesson complete
            </span>
          )}
          {!quiz && !nav.next && !complete && (
            <button
              onClick={() => void markComplete()}
              className="rounded-md border border-[var(--accept)] px-4 py-2 text-sm font-semibold"
              style={{ color: "var(--accept)" }}
            >
              Mark complete
            </button>
          )}
          {nav.next && (
            <button
              onClick={() => void goNext()}
              className="rounded-md bg-[var(--action)] px-4 py-2 text-sm font-semibold text-white"
            >
              Next lesson →
            </button>
          )}
        </div>
      </footer>
    </main>
  );
}
