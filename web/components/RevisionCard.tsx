"use client";

import { useState } from "react";
import type { Revision, Finding } from "@/lib/api";

/* ---------- content rendering ---------- */

type Content = Record<string, unknown>;

function BlockContent({ content }: { content: Content | null }) {
  if (!content) return <p className="text-[var(--muted)]">—</p>;

  if (typeof content.markdown === "string")
    return <div className="content-mono">{content.markdown}</div>;

  if (typeof content.text === "string")
    return (
      <div className="content-mono">
        {content.text}
        {typeof content.level === "number" ? `  (h${content.level})` : ""}
      </div>
    );

  if (typeof content.situation_md === "string") {
    const choices = (content.choices ?? []) as Array<Content>;
    return (
      <div className="content-mono flex flex-col gap-2">
        <div>{String(content.situation_md)}</div>
        {choices.map((c) => (
          <div key={String(c.id)} className="border-l-2 border-[var(--line)] pl-2">
            <span className="font-medium">
              [{String(c.quality)}] {String(c.text)}
            </span>
            {"\n"}→ {String(c.consequence_md)}
          </div>
        ))}
      </div>
    );
  }

  if (typeof content.alt_text === "string")
    return (
      <div className="content-mono">
        alt: {String(content.alt_text)}
        {"\n"}caption: {String(content.caption ?? "—")}
      </div>
    );

  return (
    <div className="content-mono">{JSON.stringify(content, null, 2)}</div>
  );
}

/* ---------- findings ---------- */

function FindingBadge({ finding }: { finding: Finding }) {
  const color =
    finding.severity === "blocker" ? "var(--blocker)" : "var(--advisory)";
  return (
    <li className="flex items-baseline gap-2 text-sm">
      <span
        className="rounded-sm px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide text-white"
        style={{ backgroundColor: color }}
      >
        {finding.severity}
      </span>
      <span>
        <span className="font-medium">{finding.issue}</span> — {finding.detail}
      </span>
    </li>
  );
}

/* ---------- the card ---------- */

const statusTint: Record<Revision["review_status"], string> = {
  pending: "transparent",
  accepted: "var(--accept-bg)",
  edited: "var(--edit-bg)",
  rejected: "var(--reject-bg)",
};

export function RevisionCard({
  revision,
  onReview,
}: {
  revision: Revision;
  onReview: (
    action: "accepted" | "rejected" | "edited",
    editedContent?: Record<string, unknown>,
  ) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const decided = revision.review_status !== "pending";

  async function act(
    action: "accepted" | "rejected" | "edited",
    editedContent?: Record<string, unknown>,
  ) {
    setBusy(true);
    setError(null);
    try {
      await onReview(action, editedContent);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  function startEdit() {
    setDraft(JSON.stringify(revision.generated.content, null, 2));
    setEditing(true);
  }

  function saveEdit() {
    try {
      const parsed = JSON.parse(draft) as Record<string, unknown>;
      void act("edited", parsed);
    } catch {
      setError("Edited content is not valid JSON.");
    }
  }

  return (
    <article
      className="rounded-lg border border-[var(--line)] bg-[var(--panel)]"
      style={{ backgroundColor: decided ? statusTint[revision.review_status] : undefined }}
    >
      <div className="grid grid-cols-1 gap-0 md:grid-cols-2">
        <section className="border-b border-[var(--line)] p-4 md:border-b-0 md:border-r">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Original
          </h3>
          <BlockContent content={revision.original} />
        </section>

        <section className="adapted-edge p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Adapted
            {revision.generated.unchanged ? " · returned unchanged" : ""}
          </h3>
          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={12}
              className="content-mono w-full rounded-md border border-[var(--line)] p-2"
              aria-label="Edit adapted content as JSON"
            />
          ) : (
            <BlockContent content={revision.generated.content} />
          )}
          {revision.generated.reason && (
            <p className="mt-2 text-sm text-[var(--muted)]">
              Reason: {revision.generated.reason}
            </p>
          )}
          {revision.generated.image_note && (
            <p className="mt-2 rounded-md bg-[var(--paper)] p-2 text-sm">
              <span className="font-medium">Image note:</span>{" "}
              {revision.generated.image_note}
            </p>
          )}
        </section>
      </div>

      {revision.verifier_notes.length > 0 && (
        <ul className="flex flex-col gap-1 border-t border-[var(--line)] px-4 py-3">
          {revision.verifier_notes.map((f, i) => (
            <FindingBadge key={i} finding={f} />
          ))}
        </ul>
      )}

      <footer className="flex flex-wrap items-center gap-2 border-t border-[var(--line)] px-4 py-3">
        {decided ? (
          <span className="text-sm font-semibold capitalize">
            {revision.review_status}
          </span>
        ) : editing ? (
          <>
            <button
              onClick={saveEdit}
              disabled={busy}
              className="rounded-md bg-[var(--action)] px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
            >
              Save edit
            </button>
            <button
              onClick={() => setEditing(false)}
              className="rounded-md border border-[var(--line)] px-3 py-1.5 text-sm"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => void act("accepted")}
              disabled={busy}
              className="rounded-md px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
              style={{ backgroundColor: "var(--accept)" }}
            >
              Accept
            </button>
            <button
              onClick={startEdit}
              disabled={busy}
              className="rounded-md border border-[var(--line)] px-3 py-1.5 text-sm font-semibold"
            >
              Edit
            </button>
            <button
              onClick={() => void act("rejected")}
              disabled={busy}
              className="rounded-md px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
              style={{ backgroundColor: "var(--reject)" }}
            >
              Reject
            </button>
          </>
        )}
        {error && (
          <span className="text-sm text-[var(--reject)]">{error}</span>
        )}
      </footer>
    </article>
  );
}
