"""Remove all non-equipment rows from the `documents` collection only.

Deletes rows where ``category != "equipment"`` (e.g. DailyMed ``medication``
chunks). Does **not** drop other collections, re-embed, or re-insert equipment —
existing equipment documents are left as-is.

Use when the full seed added medications and you want the references catalog
trimmed back to apparatus / tools only.

    python -m scripts.purge_documents_equipment_only

Requires the same ``.env`` / ``MONGODB_*`` as the backend.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend.mongo import collection, init_collections

log = logging.getLogger("purge.documents")


async def main() -> int:
    await init_collections()
    before = await collection("documents").count_documents({})
    res = await collection("documents").delete_many({"category": {"$ne": "equipment"}})
    after = await collection("documents").count_documents({})
    log.info(
        "documents: before=%d deleted_non_equipment=%d after=%d (equipment rows kept)",
        before,
        res.deleted_count,
        after,
    )
    print(
        f"OK: removed {res.deleted_count} non-equipment document(s); "
        f"{after} equipment row(s) remain (was {before} total).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    raise SystemExit(asyncio.run(main()))
