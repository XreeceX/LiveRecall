.PHONY: install backend worker seed seed-equipment dashboard phone mock clean

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

clean:
	rm -rf .venv dashboard/node_modules dashboard/.next
