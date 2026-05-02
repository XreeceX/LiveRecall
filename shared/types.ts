// LiveRecall shared types — TypeScript mirror of shared/types.py.
// Kept hand-in-sync; the dashboard imports from here.

export const EMBED_DIM = 1536;

export const COL = {
  SESSIONS: "sessions",
  VIDEO_FRAMES: "video_frames",
  SCENE_CONTEXT: "scene_context",
  TRANSCRIPTS: "transcripts",
  DOCUMENTS: "documents",
  CLINICAL_EVENTS: "clinical_events",
  PATIENTS: "patients",
  RETRIEVAL_PLANS: "retrieval_plans",
  RETRIEVAL_RESULTS: "retrieval_results",
  FINAL_CONTEXT: "final_context",
  ANSWERS: "answers",
  QUESTIONS: "questions",
  AGENT_TRACES: "agent_traces",
} as const;

export const MONGO_FEATURES = [
  "document_model",
  "atlas_vector_search",
  "time_series",
  "change_streams",
  "vector_search_in_aggregation",
  "gridfs",
  "ttl_indexes",
  "aggregation_pipelines",
] as const;

export type MongoFeature = (typeof MONGO_FEATURES)[number];

export type RetrievalSource = "references" | "events" | "notes";

// Two parallel capture entry points feed the same Vision pipeline:
//   "glasses" — Meta Ray-Ban first-person POV (preferred, hands-free).
//   "phone"   — universal fallback. Every clinician already has one.
// Default to "phone" when unset (safer fallback). See DECISIONS.md (g).
export type CaptureMode = "glasses" | "phone";
export const DEFAULT_CAPTURE_MODE: CaptureMode = "phone";

export interface Session {
  _id: string;
  room: string;
  started_at: number;
  ended_at: number | null;
  capture_mode?: CaptureMode;
}

export interface SceneContext {
  _id: string;
  session_id: string;
  timestamp: number;
  source_frame_id: string;
  objects: string[];
  apparatus: string[];          // canonical names matching the catalog
  text_visible: string[];
  environment: string;
  activity: string;
  text_summary: string;
  // Stamped by the backend on every scene_context insert; lets the dashboard
  // show GLASSES vs PHONE without an extra fetch.
  capture_mode?: CaptureMode;
}

export type ApparatusCategory = "medication" | "equipment" | "other";

// Unified visual+text apparatus catalog row backing the References retriever.
// Each row pairs a canonical name + clinical context + (usually) a small
// downsized image so Vision can match what the clinician is looking at.
export interface CatalogDocument {
  _id: string;
  name: string;
  category: ApparatusCategory;
  text: string;
  source_doc: string;
  section?: string;
  medication?: string;
  image_b64?: string;          // downsized JPEG, base64
  image_mime?: string;
  image_attribution?: string;
  image_source_url?: string;
  _provenance?: string;
}

export interface Transcript {
  _id: string;
  session_id: string;
  timestamp: number;
  text: string;
  is_final: boolean;
  is_question: boolean;
}

export interface RetrievalQuery {
  source: RetrievalSource;
  filter: Record<string, unknown>;
  vector_query: string;
  weight: number;
}

export interface RetrievalPlan {
  _id: string;
  question_id: string;
  session_id: string;
  question_text: string;
  scene_context_ids: string[];
  queries: RetrievalQuery[];
  created_at: number;
}

export interface RetrievalResultItem {
  document_id: string;
  score: number;
  snippet: string;
  metadata: Record<string, unknown> & { from_cache?: boolean };
}

export interface RetrievalResult {
  _id: string;
  plan_id: string;
  question_id: string;
  source: RetrievalSource;
  results: RetrievalResultItem[];
  latency_ms: number;
  from_cache: boolean;
  created_at: number;
}

export interface RankedResult {
  snippet: string;
  source: RetrievalSource;
  boosted_score: number;
  boost_reason: string;
  document_id: string;
  metadata: Record<string, unknown> & {
    from_cache?: boolean;
    from_active_followup?: boolean;
  };
}

// Active Retrieval — one bounded round of targeted follow-up queries the
// Reranker may request when it spots a concrete information gap.
export type ActiveTool =
  | "get_latest_lab"
  | "get_last_administration"
  | "get_monograph_section";

export interface ActiveFollowup {
  tool: ActiveTool;
  args: Record<string, unknown>;
  reason: string;
}

export interface ActiveFollowupResult {
  tool: ActiveTool;
  args: Record<string, unknown>;
  reason: string;
  snippet: string;
  metadata: Record<string, unknown>;
  latency_ms: number;
}

export interface FinalContext {
  _id: string;
  question_id: string;
  session_id: string;
  ranked_results: RankedResult[];
  active_followups: ActiveFollowupResult[];
  rerank_passes: number;
  created_at: number;
}

export interface Answer {
  _id: string;
  question_id: string;
  session_id: string;
  text: string;
  confidence: number;
  citations: string[];
  audio_track_id: string | null;
  created_at: number;
}

export interface AgentTrace {
  _id: string;
  question_id: string | null;
  session_id: string | null;
  agent: string;
  stage: "start" | "end";
  model: string | null;
  tokens: { input?: number; output?: number; total?: number } | null;
  latency_ms: number | null;
  timestamp: number;
  payload: Record<string, unknown> | null;
}

export interface ClinicalEvent {
  timestamp: number;
  patient_id: string;
  event_type: "med_administration" | "vitals" | "lab_result" | "note";
  severity: "low" | "medium" | "high";
  notes: string;
  medication?: string | null;
  dose?: string | null;
  lab_name?: string | null;
  lab_value?: number | null;
  lab_unit?: string | null;
}

export interface Patient {
  _id: string;
  patient_id: string;
  name: string;
  age: number;
  weight_kg: number;
  allergies: string[];
  active_conditions: string[];
  enrolled_at: number;
}

export interface ChangeStreamEvent {
  collection: string;
  operation: "insert" | "update" | "replace" | "delete" | "hello";
  document_id: string;
  doc?: unknown;
  ts: number;
}
