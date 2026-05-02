.PHONY: install backend worker seed seed-equipment dashboard phone mock bridge-rayban bridge-rayban-pick bridge-rayban-mp4 clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt
	npm install
	cd dashboard && npm install

backend:
	. .venv/bin/activate && python -m backend.main

worker:
	. .venv/bin/activate && python -m backend.worker dev

seed:
	. .venv/bin/activate && python -m backend.mongo && python -m scripts.seed_mongo

# Equipment-only seed: drops every other collection and loads ONLY the
# Wikimedia equipment fixture into `documents`. See EQUIPMENT_SCOPE.md.
seed-equipment:
	. .venv/bin/activate && python -m backend.mongo && python -m scripts.seed_equipment_only

dashboard:
	cd dashboard && npm run dev

mock:
	node scripts/mock_ws.mjs

phone:
	cd phone && python -m http.server 8080

# Meta Ray-Ban bridge (PRIMARY): screenshot the Meta AI iPhone-mirror
# window every N seconds and POST it to /snap with capture_mode=glasses.
# Pick the region once with `make bridge-rayban-pick`, then re-run with
# REGION=x,y,w,h. SESSION controls the session_id stamped on each snap.
REGION  ?=
INTERVAL ?= 3
SESSION  ?= demo
bridge-rayban:
	. .venv/bin/activate && python -m scripts.bridge_rayban_snap \
	  $(if $(REGION),--region $(REGION),--fullscreen) \
	  --interval $(INTERVAL) --session-id $(SESSION)

bridge-rayban-pick:
	. .venv/bin/activate && python -m scripts.bridge_rayban_snap --pick

# Legacy MP4 bridge: publishes a pre-recorded Ray-Ban clip into a LiveKit
# room as the "glasses" participant. Superseded by the screenshot bridge
# above for the live demo — kept around for fully-deterministic replays.
MP4 ?= data/rayban_demo.mp4
ROOM ?= liverecall-demo
bridge-rayban-mp4:
	. .venv/bin/activate && python -m scripts.bridge_rayban $(MP4) --room $(ROOM) --loop

clean:
	rm -rf .venv dashboard/node_modules dashboard/.next
