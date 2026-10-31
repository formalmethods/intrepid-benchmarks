# The benchmarks of intrepid. See README.md.
#
#   make setup       a virtual environment, .venv, with intrepyd and matplotlib
#   make check       the tests of the harnesses (neither intrepyd nor the suites needed)
#   make simulink    the Simulink benchmark: fetch, run, the reported results, the report
#   make lustre      the Lustre benchmark: intrepyd's portfolio on the Kind2 suite, and its report

PYTHON ?= .venv/bin/python
# what make setup installs: a version from PyPI, or a path to a wheel
INTREPYD ?= intrepyd>=1.0.1
RESULTS ?= results
# seconds per property, and the modes of intrepid (see simulink/harness.py)
TIMEOUT ?= 120
MODES ?= float,real
# a regular expression on the models' paths, to run some only
FILTER ?=
# the Lustre benchmark: seconds per model, and the tools (kind2 and its
# variants need KIND2, the Kind2 executable)
LUSTRE_TIMEOUT ?= 30
LUSTRE_TOOLS ?= portfolio

.PHONY: setup check fetch simulink simulink-run simulink-reported simulink-report lustre lustre-run lustre-report

setup:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install "$(INTREPYD)" matplotlib

check:
	$(PYTHON) -m unittest discover -s tests

fetch:
	$(PYTHON) simulink/harness.py fetch

simulink: fetch simulink-run simulink-reported simulink-report

simulink-run:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py run $(RESULTS)/simulink.jsonl --modes $(MODES) --timeout $(TIMEOUT) \
		$(if $(FILTER),--filter '$(FILTER)',)

simulink-reported:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py reported $(RESULTS)/reported.json

simulink-report:
	$(PYTHON) simulink/harness.py report $(RESULTS)/simulink.jsonl --reported $(RESULTS)/reported.json \
		--out $(RESULTS)/simulink.md

lustre: lustre-run lustre-report

lustre-run:
	@mkdir -p $(RESULTS)
	$(PYTHON) lustre/harness.py run $(RESULTS)/lustre.jsonl --tools $(LUSTRE_TOOLS) --timeout $(LUSTRE_TIMEOUT) \
		$(if $(FILTER),--filter '$(FILTER)',)

lustre-report:
	$(PYTHON) lustre/harness.py report $(RESULTS)/lustre.jsonl --expected lustre/kind2-benchmarks.txt
