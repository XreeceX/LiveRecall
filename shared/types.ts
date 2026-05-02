// LiveRecall shared types — TypeScript mirror of shared/types.py.
// Kept hand-in-sync; the dashboard imports from here.

export const EMBED_DIM = 1536;

export const COL = {
  SESSIONS: "sessions",
  VIDEO_FRAMES: "video_frames",
  SCENE_CONTEXT: "scene_context",
  TRANSCRIPTS: "transcripts",
  DOCUMENTS: "documents",
  MAINTENANCE_EVENTS: "maintenance_events",
  RETRIEVAL_PLANS: "retrieval_plans",
  RETRIEVAL_RESULTS: "retrieval_results",
  FINAL_CONTEXT: "final_context",
  ANSWERS: "answers",
  QUESTIONS: "questions",
  AGENT_TRACES: "agent_traces",
  MACHINES: "machines",
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

export type RetrievalSource = "manuals" | "logs" | "history";

export interface SceneContext {
  _id: string;
  session_id: string;
  timestamp: number;
  source_frame_id: string;
  objects: string[];
  text_visible: string[];
  environment: string;
  activity: string;
  text_summary: string;
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
  metadata: Record<string, unknown>;
}

export interface RetrievalResult {
  _id: string;
  plan_id: string;
  question_id: string;
  source: RetrievalSource;
  results: RetrievalResultItem[];
  latency_ms: number;
  created_at: number;
}

export interface RankedResult {
  snippet: string;
  source: RetrievalSource;
  boosted_score: number;
  boost_reason: string;
  document_id: string;
  metadata: Record<string, unknown>;
}

export interface FinalContext {
  _id: string;
  question_id: string;
  session_id: string;
  ranked_results: RankedResult[];
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

export interface MaintenanceEvent {
  timestamp: number;
  machine_id: string;
  event_type: "service" | "alert" | "inspection";
  severity: "low" | "medium" | "high";
  notes: string;
}

export interface Machine {
  _id: string;
  machine_id: string;
  kind: string;
  location: string;
  spec_failure_rate: number;
  installed_at: number;
}

export interface ChangeStreamEvent {
  collection: string;
  operation: "insert" | "update" | "replace" | "delete";
  document_id: string;
  doc?: unknown;
  ts: number;
}
