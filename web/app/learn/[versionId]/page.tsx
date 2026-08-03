"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import {
  ensureEnrollment, fetchProgress, fetchTree, fetchVersion,
  type ModuleRow, type ProgressMap, type VersionInfo,
} from "@/lib/learn";

export default function CourseOverview() {
  const { versionId } = useParams<{ versionId: string }>();
  const router = useRouter();
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [tree, setTree] = useState<ModuleRow[] | null>(null);
  const [progress, setProgress] = useState<ProgressMap>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [v, t] = await Promise.all([fetchVersion(versionId), fetchTree(versionId)]);
      setVersion(v);
      setTree(t);
      const enrollmentId = await ensureEnrollment(versionId);
      setProgress(await fetchProgress(enrollmentId));
    } catch (e) {
      const msg = (e as { message?: string })?.message;
      setError(msg ?? "Failed to load course");
    }
  }, [versionId]);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
      else void load();
    });
  }, [load, router]);

  if (error)
    return <main className="mx-auto max-w-2xl px-6 py-16 text-sm text-[var(--reject)]">{error}</main>;
  if (!version || !tree)
    return <main className="mx-auto max-w-2xl px-6 py-16 text-sm text-[var(--muted)]">Loading course…</main>;

  const allLessons = tree.flatMap((m) => m.lessons);
  const completed = allLessons.filter((l) => progress[l.id] === "complete").length;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <p className="text-xs uppercase tracking-wide text-[var(--muted)]">
        {version.label} edition
      </p>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">
        {version.course.title}
      </h1>
      {version.course.description && (
        <p className="mt-2 text-[var(--muted)]">{version.course.description}</p>
      )}

      <div className="mt-4 flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--line)]">
          <div
            className="h-full rounded-full"
            style={{
              width: `${allLessons.length ? (completed / allLessons.length) * 100 : 0}%`,
              background: "var(--spectrum)",
            }}
          />
        </div>
        <span className="text-sm text-[var(--muted)]">
          {completed}/{allLessons.length} lessons
        </span>
      </div>

      {allLessons.length > 0 && completed === allLessons.length && (
        <p
          className="mt-3 rounded-md px-4 py-2 text-sm font-semibold"
          style={{ backgroundColor: "var(--accept-bg)", color: "var(--accept)" }}
        >
          Course complete — every lesson finished.
        </p>
      )}

      {version.learning_objectives.length > 0 && (
        <section className="mt-8 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            What you'll be able to do
          </h2>
          <ul className="mt-2 list-disc pl-5 text-sm leading-relaxed">
            {version.learning_objectives.map((o, i) => <li key={i}>{o}</li>)}
          </ul>
        </section>
      )}

      <div className="mt-8 flex flex-col gap-6">
        {tree.map((mod, mi) => (
          <section key={mod.id}>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
              Module {mi + 1} · {mod.title}
            </h2>
            <ol className="mt-2 flex flex-col gap-2">
              {mod.lessons.map((lesson) => {
                const status = progress[lesson.id] ?? "not_started";
                return (
                  <li key={lesson.id}>
                    <Link
                      href={`/learn/${versionId}/lesson/${lesson.id}`}
                      className="flex items-center justify-between rounded-lg border border-[var(--line)] bg-[var(--panel)] px-4 py-3 hover:border-[var(--action)]"
                    >
                      <span className="font-medium">{lesson.title}</span>
                      <span className="flex items-center gap-3 text-sm text-[var(--muted)]">
                        {lesson.estimated_minutes && <span>{lesson.estimated_minutes} min</span>}
                        <span
                          className="rounded-full px-2 py-0.5 text-xs font-semibold"
                          style={{
                            backgroundColor:
                              status === "complete" ? "var(--accept-bg)"
                                : status === "in_progress" ? "var(--edit-bg)" : "var(--paper)",
                            color:
                              status === "complete" ? "var(--accept)"
                                : status === "in_progress" ? "var(--edit)" : "var(--muted)",
                          }}
                        >
                          {status === "complete" ? "Complete"
                            : status === "in_progress" ? "In progress" : "Not started"}
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ol>
          </section>
        ))}
      </div>
    </main>
  );
}
