"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { BlockRow } from "@/lib/learn";

function Md({ children }: { children: string }) {
  return (
    <div className="prose-refract">
      <ReactMarkdown>{children}</ReactMarkdown>
    </div>
  );
}

/* ---------- interactive: knowledge check ---------- */

function KnowledgeCheck({ content }: { content: Record<string, unknown> }) {
  const options = (content.options ?? []) as {
    id: string; text: string; correct: boolean; feedback: string;
  }[];
  const [picked, setPicked] = useState<string | null>(null);
  const chosen = options.find((o) => o.id === picked);

  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--action)]">
        Knowledge check
      </p>
      <p className="mt-2 font-medium">{String(content.question)}</p>
      <div className="mt-3 flex flex-col gap-2" role="radiogroup" aria-label="Knowledge check options">
        {options.map((o) => (
          <button
            key={o.id}
            role="radio"
            aria-checked={picked === o.id}
            onClick={() => setPicked(o.id)}
            className="rounded-md border px-3 py-2 text-left text-sm"
            style={{
              borderColor:
                picked === o.id
                  ? o.correct ? "var(--accept)" : "var(--reject)"
                  : "var(--line)",
              backgroundColor:
                picked === o.id
                  ? o.correct ? "var(--accept-bg)" : "var(--reject-bg)"
                  : "transparent",
            }}
          >
            {o.text}
          </button>
        ))}
      </div>
      {chosen && (
        <p
          className="mt-3 rounded-md p-3 text-sm"
          role="status"
          style={{
            backgroundColor: chosen.correct ? "var(--accept-bg)" : "var(--reject-bg)",
            color: chosen.correct ? "var(--accept)" : "var(--reject)",
          }}
        >
          {chosen.correct ? "Correct. " : "Not quite. "}
          {chosen.feedback}
        </p>
      )}
    </div>
  );
}

/* ---------- interactive: scenario ---------- */

function Scenario({ content }: { content: Record<string, unknown> }) {
  const choices = (content.choices ?? []) as {
    id: string; text: string; consequence_md: string;
    quality: "best" | "acceptable" | "poor";
  }[];
  const [picked, setPicked] = useState<string | null>(null);
  const chosen = choices.find((c) => c.id === picked);
  const qualityColor = { best: "var(--accept)", acceptable: "var(--edit)", poor: "var(--reject)" };
  const qualityBg = { best: "var(--accept-bg)", acceptable: "var(--edit-bg)", poor: "var(--reject-bg)" };

  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--action)]">
        Scenario
      </p>
      <div className="mt-2"><Md>{String(content.situation_md)}</Md></div>
      <div className="mt-3 flex flex-col gap-2">
        {choices.map((c) => (
          <button
            key={c.id}
            onClick={() => setPicked(c.id)}
            aria-pressed={picked === c.id}
            className="rounded-md border border-[var(--line)] px-3 py-2 text-left text-sm"
            style={picked === c.id ? { borderColor: qualityColor[c.quality] } : undefined}
          >
            {c.text}
          </button>
        ))}
      </div>
      {chosen && (
        <div
          className="mt-3 rounded-md p-3 text-sm"
          role="status"
          style={{ backgroundColor: qualityBg[chosen.quality] }}
        >
          <span className="font-semibold" style={{ color: qualityColor[chosen.quality] }}>
            {chosen.quality === "best" ? "Best choice."
              : chosen.quality === "acceptable" ? "Workable, not ideal." : "Risky choice."}
          </span>{" "}
          <Md>{chosen.consequence_md}</Md>
          {chosen.quality !== "best" && (
            <button
              onClick={() => setPicked(null)}
              className="mt-2 text-sm underline underline-offset-4"
            >
              Try another approach
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- static blocks ---------- */

export function Block({ block }: { block: BlockRow }) {
  const c = block.content;
  switch (block.block_type) {
    case "heading": {
      const level = (c.level as number) ?? 2;
      const text = String(c.text ?? "");
      return level <= 2
        ? <h2 className="mt-8 text-xl font-semibold tracking-tight">{text}</h2>
        : <h3 className="mt-6 text-lg font-semibold">{text}</h3>;
    }
    case "text":
      return <Md>{String(c.markdown ?? "")}</Md>;
    case "callout": {
      const variant = String(c.variant ?? "tip");
      const styles: Record<string, { bg: string; edge: string; label: string }> = {
        key_idea: { bg: "var(--paper)", edge: "var(--action)", label: "Key idea" },
        tip: { bg: "var(--accept-bg)", edge: "var(--accept)", label: "Tip" },
        warning: { bg: "var(--edit-bg)", edge: "var(--edit)", label: "Warning" },
      };
      const s = styles[variant] ?? styles.tip;
      return (
        <aside
          className="rounded-r-md p-4"
          style={{ backgroundColor: s.bg, borderLeft: `3px solid ${s.edge}` }}
        >
          <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: s.edge }}>
            {s.label}
          </p>
          <Md>{String(c.markdown ?? "")}</Md>
        </aside>
      );
    }
    case "image":
      return (
        <figure className="rounded-lg border border-dashed border-[var(--line)] bg-[var(--panel)] p-6 text-center">
          {/* storage integration pending: render accessible placeholder */}
          <p className="text-sm text-[var(--muted)]">[Image] {String(c.alt_text ?? "")}</p>
          {typeof c.caption === "string" && (
            <figcaption className="mt-2 text-sm">{c.caption}</figcaption>
          )}
        </figure>
      );
    case "code":
      return (
        <pre className="content-mono overflow-x-auto rounded-lg bg-[var(--ink)] p-4 text-[var(--paper)]">
          <code>{String(c.source ?? "")}</code>
        </pre>
      );
    case "video_embed":
      return (
        <figure className="rounded-lg border border-[var(--line)] p-4">
          <p className="text-sm font-medium">{String(c.title ?? "Video")}</p>
          <p className="content-mono mt-1 text-sm text-[var(--muted)]">{String(c.url ?? "")}</p>
          {typeof c.transcript_md === "string" && (
            <details className="mt-2 text-sm">
              <summary className="cursor-pointer">Transcript</summary>
              <Md>{c.transcript_md}</Md>
            </details>
          )}
        </figure>
      );
    case "table": {
      const headers = (c.headers ?? []) as string[];
      const rows = (c.rows ?? []) as string[][];
      return (
        <table className="w-full border-collapse text-sm">
          {typeof c.caption === "string" && <caption className="mb-1 text-left text-sm text-[var(--muted)]">{c.caption}</caption>}
          <thead>
            <tr>{headers.map((h, i) => <th key={i} className="border-b-2 border-[var(--line)] p-2 text-left font-semibold">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{r.map((cell, j) => <td key={j} className="border-b border-[var(--line)] p-2">{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
    }
    case "knowledge_check":
      return <KnowledgeCheck content={c} />;
    case "scenario":
      return <Scenario content={c} />;
    case "quiz_ref":
      return null; // rendered by the lesson page via QuizRunner
    default:
      return null;
  }
}
