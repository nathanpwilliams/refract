import { supabase } from "./supabase";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Finding {
  issue: string;
  detail: string;
  severity: "blocker" | "advisory";
  block_id: string;
}

export interface Revision {
  revision_id: string;
  block_id: string;
  original: Record<string, unknown>;
  generated: {
    content: Record<string, unknown> | null;
    unchanged?: boolean;
    reason?: string | null;
    image_note?: string | null;
    video_note?: string | null;
  };
  verifier_notes: Finding[];
  review_status: "pending" | "accepted" | "edited" | "rejected";
}

export interface JobStatus {
  id: string;
  status: string;
  error: string | null;
  target_version_id: string;
  created_at: string;
  finished_at: string | null;
}

async function authHeader(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("Not signed in");
  return { Authorization: `Bearer ${token}` };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = {
    ...(await authHeader()),
    "Content-Type": "application/json",
    ...(init?.headers ?? {}),
  };
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  jobStatus: (jobId: string) => request<JobStatus>(`/adaptation/jobs/${jobId}`),

  revisions: (jobId: string) =>
    request<Revision[]>(`/adaptation/jobs/${jobId}/revisions`),

  review: (
    revisionId: string,
    action: "accepted" | "rejected" | "edited",
    editedContent?: Record<string, unknown>,
  ) =>
    request<{ revision_id: string; review_status: string }>(
      `/adaptation/revisions/${revisionId}/review`,
      {
        method: "POST",
        body: JSON.stringify({ action, edited_content: editedContent ?? null }),
      },
    ),

  publish: (versionId: string) =>
    request<{ version_id: string; status: string }>(
      `/adaptation/versions/${versionId}/publish`,
      { method: "POST" },
    ),
};
