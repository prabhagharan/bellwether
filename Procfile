api:       .venv/bin/python -m uvicorn bellwether.api.app:create_app --factory --reload --port 8000
detect:    .venv/bin/python -m bellwether.worker detect
extract:   .venv/bin/python -m bellwether.worker extract
resolve:   .venv/bin/python -m bellwether.worker resolve
measure:   .venv/bin/python -m bellwether.worker measure
discovery: .venv/bin/python -m bellwether.worker discovery
alert:     .venv/bin/python -m bellwether.worker alert
ingest:    .venv/bin/python -m bellwether.worker ingest
web:       npm --prefix frontend run dev -- -p 3000
