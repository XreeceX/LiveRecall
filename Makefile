.PHONY: install backend worker seed seed-equipment purge-documents-equipment dashboard phone tunnel-phone tunnel-backend phone-demo dev-stack bridge-rayban bridge-rayban-pick bridge-rayban-mp4 clean

# Windows venv uses Scripts/; Unix uses bin/. Use the interpreter directly so
# `make install` / `make backend` work without a POSIX-only activate path.
ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
else
VENV_PYTHON := .venv/bin/python
endif

install:
	python -m venv .venv && "$(VENV_PYTHON)" -m pip install -r backend/requirements.txt
	cd dashboard && npm install

backend:
	"$(VENV_PYTHON)" -m backend.main

worker:
	"$(VENV_PYTHON)" -m backend.worker dev

seed:
	"$(VENV_PYTHON)" -m backend.mongo && "$(VENV_PYTHON)" -m scripts.seed_mongo

# Equipment-only seed: drops every other collection and loads ONLY the
# Wikimedia equipment fixture into `documents`. See EQUIPMENT_SCOPE.md.
seed-equipment:
	"$(VENV_PYTHON)" -m backend.mongo && "$(VENV_PYTHON)" -m scripts.seed_equipment_only

# Strip medication (and any non-equipment) rows from `documents` only — keeps
# patients/transcripts/etc. intact. See scripts/purge_documents_equipment_only.py.
purge-documents-equipment:
	"$(VENV_PYTHON)" -m scripts.purge_documents_equipment_only

dashboard:
	cd dashboard && npm run dev

phone:
	cd phone && python -m http.server 8080

# Cloudflare Quick Tunnels — free HTTPS to localhost (no Cloudflare account
# required for trycloudflare.com URLs). Install cloudflared first:
#   https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
# Windows: winget install Cloudflare.cloudflared
# On Windows, GNU Make often runs without Git Bash PATH, so `cloudflared` is
# "file not found" (e=2). Default to the usual winget path (no spaces).
# Override if needed: make tunnel-phone CLOUDFLARED="C:/path/to/cloudflared.exe"
ifeq ($(OS),Windows_NT)
CLOUDFLARED ?= C:/PROGRA~2/cloudflared/cloudflared.exe
else
CLOUDFLARED ?= cloudflared
endif
# Run in *two* terminals alongside `make phone` + `make backend`:
tunnel-phone:
	$(CLOUDFLARED) tunnel --url http://localhost:8080

tunnel-backend:
	$(CLOUDFLARED) tunnel --url http://localhost:8000

# Phone over HTTPS in ONE terminal: starts both Quick Tunnels first, prints one
# glasses.html URL with ?backend=… preset, then runs dev-stack. Requires cloudflared.
# Desktop/laptop: use `make dev-stack` only (localhost — no tunnels).
phone-demo:
	"$(VENV_PYTHON)" scripts/phone_demo.py

# One terminal: API + worker + dashboard + phone static server (Ctrl+C kills all).
# Requires the same Python you use for backend (e.g. conda). Tunnels are separate.
dev-stack:
	"$(VENV_PYTHON)" scripts/dev_stack.py

# Meta Ray-Ban bridge (PRIMARY): screenshot the Meta AI iPhone-mirror
# window every N seconds and POST it to /snap with capture_mode=glasses.
# Pick the region once with `make bridge-rayban-pick`, then re-run with
# REGION=x,y,w,h. SESSION controls the session_id stamped on each snap.
REGION  ?=
INTERVAL ?= 3
SESSION  ?= demo
bridge-rayban:
	"$(VENV_PYTHON)" -m scripts.bridge_rayban_snap \
	  $(if $(REGION),--region $(REGION),--fullscreen) \
	  --interval $(INTERVAL) --session-id $(SESSION)

bridge-rayban-pick:
	"$(VENV_PYTHON)" -m scripts.bridge_rayban_snap --pick

# Legacy MP4 bridge: publishes a pre-recorded Ray-Ban clip into a LiveKit
# room as the "glasses" participant. Superseded by the screenshot bridge
# above for the live demo — kept around for fully-deterministic replays.
MP4 ?= data/rayban_demo.mp4
ROOM ?= liverecall-demo
bridge-rayban-mp4:
	"$(VENV_PYTHON)" -m scripts.bridge_rayban $(MP4) --room $(ROOM) --loop

clean:
	rm -rf .venv dashboard/node_modules dashboard/.next
