"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, type JobStatus, type Revision } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { RevisionCard } from "@/components/RevisionCard";

export default function ReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router = useRouter();

  const [job, setJob] = useState<JobStatus | null>(null);
  const [revisions, setRevisions] = useState<Revision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishState, setPublishState] = useState<
    "idle" | "publishing" | "published"
  >("idle");

  const load = useCallback(async () => {
    try {
      const [j, r] = await Promise.all([
        api.jobStatus(jobId),
        api.revisions(jobId),
      ]);
      setJob(j);
      setRevisions(r);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load";
      if (msg === "Not signed in") router.replace("/login");
      else setError(msg);
    }
  }, [jobId, router]);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
      else void load();
    });
  }, [load, router]);

  async function handleReview(
    revisionId: string,
    action: "accepted" | "rejected" | "edited",
    editedContent?: Record<string, unknown>,
  ) {
    await api.review(revisionId, action, editedContent);
    setRevisions(
      (prev) =>
        prev?.map((r) =>
          r.revision_id === revisionId ? { ...r, review_status: action } : r,
        ) ?? null,
    );
  }

  async function handlePublish() {
    if (!job) return;
    setPublishState("publishing");
    setError(null);
    try {
      await api.publish(job.target_version_id);
      setPublishState("published");
    } catch (e) {
      setPublishState("idle");
      setError(e instanceof Error ? e.message : "Publish failed");
    }
  }

  if (error && !revisions)
    return (
      <main className="mx-auto max-w-2xl px-6 py-16">
        <p className="rounded-md bg-[var(--reject-bg)] p-4 text-sm text-[var(--reject)]">
          {error}
        </p>
      </main>
    );

  if (!revisions || !job)
    return (
      <main className="mx-auto max-w-2xl px-6 py-16 text-sm text-[var(--muted)]">
        Loading review queue…
      </main>
    );

  const pending = revisions.filter((r) => r.review_status === "pending").length;

  return (
    <main className="mx-auto max-w-5xl px-4 pb-28 pt-8 md:px-6">
      <header className="mb-6">
        <p className="text-xs uppercase tracking-wide text-[var(--muted)]">
          Adaptation job
        </p>
        <h1 className="content-mono text-lg">{job.id}</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Status: {job.status} · {revisions.length} blocks ·{" "}
          {revisions.reduce((n, r) => n + r.verifier_notes.length, 0)} verifier
          findings
        </p>
      </header>

      <div className="flex flex-col gap-4">
        {revisions.map((rev) => (
          <RevisionCard
            key={rev.revision_id}
            revision={rev}
            onReview={(action, edited) =>
              handleReview(rev.revision_id, action, edited)
            }
          />
        ))}
      </div>

      <footer className="fixed inset-x-0 bottom-0 border-t border-[var(--line)] bg-[var(--panel)]/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3 md:px-6">
          <span className="text-sm">
            {publishState === "published" ? (
              <span className="font-semibold text-[var(--accept)]">
                Published
              </span>
            ) : (
              <>
                <span className="font-semibold">{pending}</span>{" "}
                <span className="text-[var(--muted)]">
                  {pending === 1 ? "revision" : "revisions"} pending review
                </span>
              </>
            )}
            {error && publishState !== "published" && (
              <span className="ml-3 text-[var(--reject)]">{error}</span>
            )}
          </span>
          <button
            onClick={() => void handlePublish()}
            disabled={pending > 0 || publishState !== "idle"}
            className="rounded-md bg-[var(--action)] px-5 py-2 text-sm font-semibold text-white disabled:opacity-40"
            title={
              pending > 0
                ? `${pending} revisions must be reviewed before publishing`
                : "Publish this version"
            }
          >
            {publishState === "publishing"
              ? "Publishing…"
              : publishState === "published"
                ? "Published"
                : "Publish version"}
          </button>
        </div>
      </footer>
    </main>
  );
}
