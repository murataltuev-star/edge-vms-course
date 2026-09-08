# Lesson 13, Step 8. Everything routes through .venv; never a bare python3.
VENV := .venv
PY := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r server/requirements.txt -r edge/requirements.txt
	$(PY) scripts/check_env.py
	$(PY) scripts/create_stream.py

stream:
	$(PY) edge/looper.py

serve:
	$(VENV)/bin/uvicorn server.app:app --port $${SERVER_PORT:-8000} --reload

test:
	$(PY) tests/run.py

clip:
	scripts/make_clip.sh

# М9: the appliance image edgevms/quadlet/vms-agent.container runs.
# Build context is the COURSE ROOT so the image can take М9's spool.py too.
agent-image:
	cd ../.. && podman build -f М8_KVS_VMS/kvsvms/docker/vms-agent/Containerfile -t localhost/example/vms-agent:1.0 .

.PHONY: setup stream serve test clip agent-image
