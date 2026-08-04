"use client";

import { useState } from "react";
import type { QuizRow } from "@/lib/learn";

export function QuizRunner({
  quiz,
  onComplete,
}: {
  quiz: QuizRow;
  onComplete: (scorePct: number, passed: boolean, answers: Record<string, string[]>) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [submitted, setSubmitted] = useState(false);
  const [result, setResult] = useState<{ scorePct: number; passed: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  function toggle(questionId: string, optionId: string, multi: boolean) {
    if (submitted) return;
    setAnswers((prev) => {
      const current = prev[questionId] ?? [];
      if (!multi) return { ...prev, [questionId]: [optionId] };
      return {
        ...prev,
        [questionId]: current.includes(optionId)
          ? current.filter((id) => id !== optionId)
          : [...current, optionId],
      };
    });
  }

  async function submit() {
    setBusy(true);
    let correct = 0;
    for (const q of quiz.questions) {
      const chosen = new Set(answers[q.id] ?? []);
      const right = new Set(q.options.filter((o) => o.correct).map((o) => o.id));
      const exact =
        chosen.size === right.size && [...chosen].every((id) => right.has(id));
      if (exact) correct += 1;
    }
    const scorePct = Math.round((correct / quiz.questions.length) * 1000) / 10;
    const passed = scorePct >= quiz.pass_pct;
    setResult({ scorePct, passed });
    setSubmitted(true);
    try {
      await onComplete(scorePct, passed, answers);
    } finally {
      setBusy(false);
    }
  }

  const allAnswered = quiz.questions.every((q) => (answers[q.id] ?? []).length > 0);

  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-5">
      <h2 className="text-lg font-semibold">{quiz.title}</h2>
      <p className="mt-1 text-sm text-[var(--muted)]">
        Passing score: {quiz.pass_pct}%
      </p>

      <ol className="mt-4 flex flex-col gap-6">
        {quiz.questions.map((q, qi) => {
          const multi = q.question_type === "multi_select";
          const chosen = answers[q.id] ?? [];
          return (
            <li key={q.id}>
              <p className="font-medium">
                {qi + 1}. {q.prompt}
                {multi && (
                  <span className="ml-2 text-xs text-[var(--muted)]">
                    (select all that apply)
                  </span>
                )}
              </p>
              <div className="mt-2 flex flex-col gap-2">
                {q.options.map((o) => {
                  const isChosen = chosen.includes(o.id);
                  const showState = submitted;
                  return (
                    <div key={o.id}>
                      <button
                        onClick={() => toggle(q.id, o.id, multi)}
                        aria-pressed={isChosen}
                        disabled={submitted}
                        className="w-full rounded-md border px-3 py-2 text-left text-sm"
                        style={{
                          borderColor: showState
                            ? o.correct ? "var(--accept)" : isChosen ? "var(--reject)" : "var(--line)"
                            : isChosen ? "var(--action)" : "var(--line)",
                          backgroundColor: showState
                            ? o.correct ? "var(--accept-bg)" : isChosen ? "var(--reject-bg)" : "transparent"
                            : isChosen ? "var(--paper)" : "transparent",
                        }}
                      >
                        {o.text}
                      </button>
                      {showState && (isChosen || o.correct) && (
                        <p className="mt-1 pl-3 text-sm text-[var(--muted)]">{o.rationale}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </li>
          );
        })}
      </ol>

      <footer className="mt-6 flex items-center gap-4">
        {!submitted ? (
          <button
            onClick={() => void submit()}
            disabled={!allAnswered || busy}
            className="rounded-md bg-[var(--action)] px-5 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            Submit answers
          </button>
        ) : (
          result && (
            <p
              className="rounded-md px-4 py-2 text-sm font-semibold"
              role="status"
              style={{
                backgroundColor: result.passed ? "var(--accept-bg)" : "var(--reject-bg)",
                color: result.passed ? "var(--accept)" : "var(--reject)",
              }}
            >
              {result.scorePct}% — {result.passed ? "Passed" : `Below the ${quiz.pass_pct}% passing score`}
            </p>
          )
        )}
      </footer>
    </section>
  );
}
