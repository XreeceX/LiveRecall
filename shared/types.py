"""LiveRecall shared types — the API contract.

Locked first. The seam between Stream A (capture/LiveKit), Stream B (agents),
Stream C (Mongo + STT/TTS), and Stream D (dashboard).

All timestamps are unix milliseconds.
Embedding dimension is 1536 (text-embedding-3-small).

Domain: point-of-care clinical decision support. See `CLAUDE (1).md` for the
demo scenario (METFORMIN 500 mg + patient P-204 with eGFR=38).
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
COL_DOCUMENTS = "documents"            # drug monographs / protocols / guidelines
COL_CLINICAL_EVENTS = "clinical_events"  # Time Series, per-patient
COL_PATIENTS = "patients"              # patient master records
COL_RETRIEVAL_PLANS = "retrieval_plans"
COL_RETRIEVAL_RESULTS = "retrieval_results"
COL_FINAL_CONTEXT = "final_context"
COL_ANSWERS = "answers"
COL_QUESTIONS = "questions"
COL_AGENT_TRACES = "agent_traces"

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

# Source labels Router emits and Retrievers dispatch on.
#   references — drug monographs / protocols / clinical guidelines (vector search)
#   events     — per-patient clinical events from the Time Series collection
#   notes      — past clinical handoffs / dictated notes (vector + recency)
RetrievalSource = Literal["references", "events", "notes"]

# Capture-source the scene came from. Two parallel entry points, same Vision
# pipeline downstream:
#   "glasses" — Meta Ray-Ban / first-person POV, hands-free, continuous video.
#   "phone"   — universal fallback. Every clinician already has one; lets the
#               whole clinic adopt without buying hardware. Default when unset.
# See DECISIONS.md (g) for the rationale and the documented path to a real
# Ray-Ban Live AI integration.
CaptureMode = Literal["glasses", "phone"]
DEFAULT_CAPTURE_MODE: CaptureMode = "phone"


class Session(TypedDict, total=False):
    _id: str
    room: str
    started_at: int
    ended_at: int | None
    # Which physical capture device the clinician is using for this session.
    # Set by /token (or the snap path) and threaded onto every scene_context.
    capture_mode: CaptureMode


class VideoFrame(TypedDict):
    _id: str
    session_id: str
    timestamp: int
    image_b64: str           # JPEG, base64-encoded; small (<150KB)
    width: int
    height: int
    source: str              # "stream" | "snap"


class SceneContext(TypedDict, total=False):
    _id: str
    session_id: str
    timestamp: int
    source_frame_id: str
    objects: list[str]              # generic object labels: ["pill bottle", "wristband", "iv pole"]
    apparatus: list[str]            # canonical names matching the catalog: ["metformin", "infusion pump"]
    text_visible: list[str]         # e.g. ["METFORMIN 500 mg", "P-204", "eGFR 38"]
    environment: str                # e.g. "hospital_room" | "clinic" | "pharmacy"
    activity: str                   # e.g. "reviewing medication"
    text_summary: str               # one short sentence used for embedding
    text_embedding: list[float]     # 1536-dim
    # Stamped by Vision on every scene_context insert; lets the dashboard show
    # GLASSES vs PHONE without an extra fetch. Defaults to "phone" when unset.
    capture_mode: CaptureMode


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
    filter: dict[str, Any]              # may contain {patient_id?: "P-204", medication?: "metformin"}
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
    metadata: dict[str, Any]              # may include {"from_cache": bool}


class RetrievalResult(TypedDict):
    _id: str
    plan_id: str
    question_id: str
    source: RetrievalSource
    results: list[RetrievalResultItem]
    latency_ms: int
    from_cache: bool                      # all items in results were served from local cache
    created_at: int


class RankedResult(TypedDict):
    snippet: str
    source: RetrievalSource
    boosted_score: float
    boost_reason: str
    document_id: str
    metadata: dict[str, Any]              # may include {"from_cache": bool, "from_active_followup": bool}


# --- Active Retrieval -------------------------------------------------------
# The Reranker may emit one bounded round of follow-up queries when it spots a
# concrete information gap (e.g. it has a renal-contraindication chunk for
# metformin, but no eGFR in the events list). The follow-ups are executed in
# parallel and a second rerank pass folds the new facts in.

ActiveTool = Literal["get_latest_lab", "get_last_administration", "get_monograph_section"]


class ActiveFollowup(TypedDict):
    tool: ActiveTool
    args: dict[str, Any]
    reason: str                           # why the Reranker thinks this gap matters


class ActiveFollowupResult(TypedDict):
    tool: ActiveTool
    args: dict[str, Any]
    reason: str
    snippet: str                          # one-line rendering for prompts + dashboard
    metadata: dict[str, Any]
    latency_ms: int


class FinalContext(TypedDict):
    _id: str
    question_id: str
    session_id: str
    ranked_results: list[RankedResult]
    active_followups: list[ActiveFollowupResult]   # [] if Reranker requested none
    rerank_passes: int                             # 1 or 2
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
    agent: str                # "vision" | "router" | "retriever:references" | ...
    stage: str                # "start" | "end"
    model: str | None
    tokens: dict[str, int] | None
    latency_ms: int | None
    timestamp: int
    payload: dict[str, Any] | None


# --- Domain entities ---------------------------------------------------------

class ClinicalEvent(TypedDict):
    timestamp: int             # Time Series timeField
    patient_id: str            # Time Series metaField (e.g. "P-204")
    event_type: str            # "med_administration" | "vitals" | "lab_result" | "note"
    severity: str              # "low" | "medium" | "high"
    notes: str
    medication: str | None     # for med_administration
    dose: str | None           # for med_administration
    lab_name: str | None       # for lab_result
    lab_value: float | None    # for lab_result
    lab_unit: str | None       # for lab_result


class Patient(TypedDict):
    _id: str
    patient_id: str            # MRN as printed on the wristband, e.g. "P-204"
    name: str                  # mock name only — never real PHI
    age: int
    weight_kg: float
    allergies: list[str]
    active_conditions: list[str]
    enrolled_at: int


# --- Multimodal apparatus catalog (References retriever) -------------------
# `documents` is a unified visual+text catalog of medications and equipment.
# Each entry follows the **(name, context, image)** shape so Vision can match
# physical apparatus the clinician is looking at:
#   - DailyMed-sourced rows have category="medication" (drug monograph chunk +
#     real product label image from the FDA Structured Product Label)
#   - Wikimedia-sourced rows have category="equipment" (Wikipedia article
#     extract + Commons device image, CC-BY-SA)
# image_b64 is a downsized JPEG (≤256 px, base64-encoded; ~5–20 KB per entry).

ApparatusCategory = Literal["medication", "equipment", "other"]


class CatalogDocument(TypedDict, total=False):
    _id: str
    name: str                       # canonical: "metformin", "infusion pump"
    category: ApparatusCategory
    text: str                       # context (monograph section / article extract)
    text_embedding: list[float]     # 1536-dim, used by $vectorSearch
    source_doc: str                 # human-readable provenance line
    section: str                    # for medications: LOINC section name
    medication: str                 # alias of name when category="medication"
    image_b64: str                  # downsized JPEG, base64
    image_mime: str                 # "image/jpeg"
    image_attribution: str          # author / license string for the image
    image_source_url: str           # original URL on dailymed / wikimedia
    _provenance: str
