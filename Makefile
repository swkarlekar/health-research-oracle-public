PYTHON ?= .venv/bin/python
BOOTSTRAP_PYTHON ?= python3

.PHONY: setup run test export-aws graph-status demo api-check serve
setup:
	$(BOOTSTRAP_PYTHON) scripts/setup_demo.py

run:
	$(BOOTSTRAP_PYTHON) scripts/run_demo.py

serve:
	$(PYTHON) -m oracle serve

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m unittest discover -s text_corpus/tests -v

export-aws:
	$(PYTHON) -m oracle export-aws

graph-status:
	$(PYTHON) -m oracle graph-status

demo:
	$(PYTHON) -m oracle demo --case 1 --retriever local-vector --timeout 600 --output artifacts/scenario-01-demo.md --debug-output artifacts/scenario-01.debug.json

api-check:
	$(PYTHON) -m oracle api-check --case 1 --view debug --output artifacts/api-check.debug.json
