.PHONY: install backend worker seed dashboard phone clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt
	cd dashboard && npm install

backend:
	. .venv/bin/activate && python -m backend.main

worker:
	. .venv/bin/activate && python -m backend.worker dev

seed:
	. .venv/bin/activate && python -m backend.mongo && python -m scripts.seed_mongo

dashboard:
	cd dashboard && npm run dev

phone:
	cd phone && python -m http.server 8080

clean:
	rm -rf .venv dashboard/node_modules dashboard/.next
