# `data/` — public-domain medical datasets

LiveRecall is backed by **four real public datasets**. The References
retriever now reads from a *unified multimodal apparatus catalog* (DailyMed
medications + Wikimedia equipment); Events and Notes each have a single
dedicated source.

| Retriever  | Dataset                                      | License                       | Source                                                  |
| ---------- | -------------------------------------------- | ----------------------------- | ------------------------------------------------------- |
| References (medications) | **DailyMed** (FDA SPL drug labels + product images) | US Government work / public   | https://dailymed.nlm.nih.gov/ (NIH/NLM)                 |
| References (equipment)   | **Wikipedia + Wikimedia Commons**            | CC-BY-SA 4.0                  | https://en.wikipedia.org/api/rest_v1/page/summary       |
| Events     | **Synthea** (synthetic patients)             | Apache 2.0                    | https://github.com/synthetichealth/synthea              |
| Notes      | **MTSamples** (medical transcriptions)       | CC0 (public domain)           | https://huggingface.co/datasets/harishnair04/mtsamples  |

Why these specifically: see [`../DECISIONS.md`](../DECISIONS.md) entries
**(e)** and **(f)**. Short version: each one publishes **canonical** data —
USAN drug names, LOINC labs, RxNorm codes, real clinical-dictation
conventions, common bedside device shapes. That canonicality is exactly
what lets our pre-trained model stack work zero-shot without any
fine-tuning per hospital.

## The multimodal apparatus catalog

The headline change in (f) is that every row in `documents` now has the
shape `(name, context, image)`:

```jsonc
{
  "name": "metformin",                 // canonical lowercase
  "category": "medication",            // | "equipment" | "other"
  "text": "...renal contraindications...",
  "image_b64": "<256px JPEG, base64>", // ~5–20 KB
  "image_mime": "image/jpeg",
  "image_attribution": "DailyMed (51655-555-96 - Rev A 12-25 NHC.jpg)",
  "image_source_url": "https://dailymed.nlm.nih.gov/dailymed/image.cfm?...",
  "source_doc": "DailyMed: METFORMIN HYDROCHLORIDE EXTENDED RELEASE ...",
  "section": "Contraindications"
}
```

The same shape covers equipment, just with `category: "equipment"` and
`source_doc: "Wikipedia: Infusion pump"`. Vision uses the `name` field as
its recognition vocabulary; the dashboard renders `image_b64` as the
inline thumbnail next to each retrieval hit.

## Layout

```
data/
  README.md                            # this file
  cache/                               # raw downloads (gitignored — refresh idempotent)
  fixtures/                            # processed JSONL the seeder reads
    dailymed_sample.jsonl              # 136 monograph chunks across 10 drugs (with product images)
    wikimedia_equipment_sample.jsonl   # ~23 bedside devices (with thumbnails)
    synthea_sample.jsonl               # 25 patients + ~917 events (Time-Series ready)
    mtsamples_sample.jsonl             # 35 endo/renal/handoff notes
```

The `fixtures/*.jsonl` files are **committed to the repo** (~2.2 MB total,
of which ~700 KB is base64-encoded thumbnails) so the demo always works
offline. The `cache/` directory is gitignored — it's where the ingest
scripts dump raw API responses, downloaded archives, and pre-downscale
JPEGs.

## Refreshing fixtures

You only need to run these once. They're idempotent — re-running just
rewrites the same fixture files. Run order doesn't matter.

```bash
# DailyMed — public REST API, no auth. Pulls per-SPL media.json, picks the
# product photo (skips structure diagrams), downscales to 256 px JPEG. ~40 s.
python -m scripts.ingest_dailymed

# Wikimedia equipment — Wikipedia REST summary endpoint + Commons thumbnails.
# Walks a curated list of ~25 apparatus titles. ~3–5 min depending on whether
# Wikimedia's CDN is rate-limiting (the script backs off on 429s).
python -m scripts.ingest_wikimedia_equipment

# MTSamples — Hugging Face Parquet, ~17 MB download. ~20 s.
python -m scripts.ingest_mtsamples

# Synthea — runs the Java generator OR downloads a pre-generated SyntheticMass
# sample. ~5 min the first time (downloads jar + generates patients).
python -m scripts.ingest_synthea
```

Then seed Mongo as usual:

```bash
python -m scripts.seed_mongo
```

`seed_mongo.py` prefers fixtures when present and falls back to a small
inline mock dataset if any fixture is missing — so the demo never breaks.

## Demo scenario fidelity

The bedside-medication-safety demo (`P-204` + `METFORMIN 500 mg` + `eGFR 38`)
is preserved by:

1. **References — medication.** DailyMed's real FDA-approved metformin label.
   The renal-contraindication text the Reranker quotes is the *actual* FDA
   text, and the dashboard shows the real NDC 51655-555-96 metformin
   bottle photo next to it.
2. **References — equipment.** When Vision sees a wristband, infusion pump,
   or glucose meter in the same scene, the equipment catalog returns the
   matching Wikipedia overview + Commons device photo.
3. **Events.** A Synthea-generated patient with type-2 diabetes + CKD-stage-3
   is selected from the fixture and **re-labelled `P-204`** with mock name
   "Sarah Chen". Their real-shaped Synthea events (medications, vitals, labs)
   become our `clinical_events`. We additionally inject the hand-shaped
   "metformin 47 h ago" + "eGFR 38 yesterday" events to guarantee the demo
   beats land deterministically.
4. **Notes.** A handful of relevant MTSamples notes (endocrinology +
   nephrology) provide realistic clinical-dictation prose for the Notes
   retriever's vector search.

## Compliance / safety

- DailyMed + Synthea + MTSamples are all **public domain or open source**.
- Wikimedia text is **CC-BY-SA 4.0**; image licenses vary per file but are
  all free-culture (CC-BY-SA, CC0, or PD). Each row carries
  `image_attribution` + `image_source_url` so the dashboard can credit the
  author. If you redistribute the fixtures, keep these fields intact.
- Synthea data is **synthetic** — no patient ever existed. By construction,
  there is no PHI risk.
- MTSamples were de-identified at source by mtsamples.com before publication.
- Patient names in fixtures are mock. Any resemblance to real persons is
  coincidental.
- Pitch language remains **decision support, never diagnosis**. See
  `CLAUDE (1).md` "Compliance note" and `DECISIONS.md` (b).
