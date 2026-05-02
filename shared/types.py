"""LiveRecall shared types — the API contract.

Locked first. The seam between Stream A (capture/LiveKit), Stream B (agents),
Stream C (Mongo + STT/TTS), and Stream D (dashboard).

All timestamps are unix milliseconds.
Embedding dimension is 1536 (text-embedding-3-small).
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

EMBED_DIM = 1536

# Mongo collections (kept here so every stream agrees on names).
COL_SESSIONS = "sessions"
COL_CLIPS_FILES = "clips.files"        # GridFS
COL_CLIPS_CHUNKS = "clips.chunks"      # GridFS
COL_VIDEO_FRAMES = "video_frames"
COL_SCENE_CONTEXT = "scene_context"
COL_TRANSCRIPTS = "transcripts"
COL_DOCUMENTS = "documents"
COL_MAINTENANCE_EVENTS = "maintenance_events"  # Time Series
COL_RETRIEVAL_PLANS = "retrieval_plans"
COL_RETRIEVAL_RESULTS = "retrieval_results"
COL_FINAL_CONTEXT = "final_context"
COL_ANSWERS = "answers"
COL_QUESTIONS = "questions"
COL_AGENT_TRACES = "agent_traces"
COL_MACHINES = "machines"

# Vector search index names — must match what mongo.py creates in Atlas.
VEC_IDX_SCENE = "scene_text_vec"
VEC_IDX_DOCS = "doc_text_vec"
VEC_IDX_TRANSCRIPTS = "transcript_text_vec"

# Mongo features the dashboard sidebar tracks. Each turns green when first used.
MONGO_FEATURES = [
    "document_model",
    "atlas_vector_search",
    "time_series",
    "change_streams",
    "vector_search_in_aggregation",
    "gridfs",
    "ttl_indexes",
    "aggregation_pipelines",
]

RetrievalSource = Literal["manuals", "logs", "history"]


class Session(TypedDict):
    _id: str
    room: str
    started_at: int
    ended_at: int | None


class VideoFrame(TypedDict):
    _id: str
    session_id: str
    timestamp: int
    image_b64: str           # JPEG, base64-encoded; small (<150KB)
    width: int
    height: int


class SceneContext(TypedDict):
    _id: str
    session_id: str
    timestamp: int
    source_frame_id: str
    objects: list[str]              # e.g. ["conveyor belt", "pressure gauge"]
    text_visible: list[str]         # e.g. ["C-204", "PSI 47"]
    environment: str                # e.g. "factory_floor"
    activity: str                   # e.g. "running"
    text_summary: str               # one short sentence used for embedding
    text_embedding: list[float]     # 1536-dim


class Transcript(TypedDict):
    _id: str
    session_id: str
    timestamp: int
    text: str
    is_final: bool
    is_question: bool
    text_embedding: list[float] | None  # only on final segments


class Question(TypedDict):
    _id: str
    session_id: str
    transcript_id: str
    text: str
    asked_at: int


class RetrievalQuery(TypedDict):
    source: RetrievalSource
    filter: dict[str, Any]
    vector_query: str
    weight: float


class RetrievalPlan(TypedDict):
    _id: str
    question_id: str
    session_id: str
    question_text: str
    scene_context_ids: list[str]
    queries: list[RetrievalQuery]
    created_at: int


class RetrievalResultItem(TypedDict):
    document_id: str
    score: float
    snippet: str
    metadata: dict[str, Any]


class RetrievalResult(TypedDict):
    _id: str
    plan_id: str
    question_id: str
    source: RetrievalSource
    results: list[RetrievalResultItem]
    latency_ms: int
    created_at: int


class RankedResult(TypedDict):
    snippet: str
    source: RetrievalSource
    boosted_score: float
    boost_reason: str
    document_id: str
    metadata: dict[str, Any]


class FinalContext(TypedDict):
    _id: str
    question_id: str
    session_id: str
    ranked_results: list[RankedResult]
    created_at: int


class Answer(TypedDict):
    _id: str
    question_id: str
    session_id: str
    text: str
    confidence: float
    citations: list[str]
    audio_track_id: str | None
    created_at: int


class AgentTrace(TypedDict):
    _id: str
    question_id: str | None
    session_id: str | None
    agent: str                # "vision" | "router" | "retriever:manuals" | ...
    stage: str                # "start" | "end"
    model: str | None
    tokens: dict[str, int] | None
    latency_ms: int | None
    timestamp: int
    payload: dict[str, Any] | None


class MaintenanceEvent(TypedDict):
    timestamp: int             # Time Series timeField
    machine_id: str            # Time Series metaField
    event_type: str            # "service" | "alert" | "inspection"
    severity: str              # "low" | "medium" | "high"
    notes: str


class Machine(TypedDict):
    _id: str
    machine_id: str            # "C-204"
    kind: str
    location: str
    spec_failure_rate: float
    installed_at: int
