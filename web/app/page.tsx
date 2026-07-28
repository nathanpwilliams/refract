"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function HomePage() {
  const router = useRouter();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [jobId, setJobId] = useState("");

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
      else setSignedIn(true);
    });
  }, [router]);

  if (signedIn === null) return null;

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Refract review</h1>
      <p className="mt-1 text-sm text-[var(--muted)]">
        Paste an adaptation job ID to open its review queue.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (jobId.trim()) router.push(`/review/${jobId.trim()}`);
        }}
        className="mt-6 flex gap-2"
      >
        <input
          value={jobId}
          onChange={(e) => setJobId(e.target.value)}
          placeholder="920a338c-3e5c-…"
          className="content-mono flex-1 rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2"
          aria-label="Adaptation job ID"
        />
        <button
          type="submit"
          className="rounded-md bg-[var(--action)] px-4 py-2 text-sm font-semibold text-[var(--action-ink)]"
        >
          Open queue
        </button>
      </form>

      <button
        onClick={async () => {
          await supabase.auth.signOut();
          router.replace("/login");
        }}
        className="mt-10 self-start text-sm text-[var(--muted)] underline underline-offset-4"
      >
        Sign out
      </button>
    </main>
  );
}
