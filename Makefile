# The benchmarks of intrepid. See README.md.
#
#   make setup       a virtual environment, .venv, with intrepyd and matplotlib
#   make check       the tests of the harnesses (neither intrepyd nor the suites needed)
#   make simulink    the Simulink benchmark: fetch, run, the reported results, the report
#   make simulink-tools  SLDV, CoCoSim and Kind2 measured too (MATLAB, KIND2, Z3), then the report
#   make simulink-all    intrepid, SLDV, CoCoSim and Kind2, then the report
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
# the other tools of the Simulink benchmark: MATLAB with Simulink Design
# Verifier, for SLDV and CoCoSim (its executable, or a launcher of it), and
# Kind2 with its z3, for CoCoSim and for Kind2 alone
MATLAB ?= matlab
KIND2 ?= kind2
Z3 ?= z3
# the Kind2 of CoCoSim's installer, v1.7.0, for CoCoSim's Lustre (newer
# ones reject its library); the models whose Lustre it rejects are retried
# with KIND2
COCOSIM_KIND2 ?= $(KIND2)
COCOSIM_FALLBACK = $(if $(filter-out $(KIND2),$(COCOSIM_KIND2)),--fallback-kind2 '$(KIND2)')
# the Lustre benchmark: seconds per model, and the tools (kind2 and its
# variants need KIND2, the Kind2 executable)
LUSTRE_TIMEOUT ?= 30
LUSTRE_TOOLS ?= portfolio

.PHONY: setup check fetch fetch-cocosim simulink simulink-run simulink-reported simulink-report \
	simulink-tools simulink-all simulink-sldv simulink-cocosim simulink-kind2 lustre lustre-run lustre-report

setup:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install "$(INTREPYD)" matplotlib

check:
	$(PYTHON) -m unittest discover -s tests

fetch:
	$(PYTHON) simulink/harness.py fetch

fetch-cocosim:
	$(PYTHON) simulink/harness.py fetch-cocosim

simulink: fetch simulink-run simulink-reported simulink-report

simulink-all: fetch fetch-cocosim simulink-run simulink-kind2 simulink-sldv simulink-cocosim simulink-reported \
	simulink-report

simulink-tools: fetch fetch-cocosim simulink-kind2 simulink-sldv simulink-cocosim simulink-reported simulink-report

FILTER_OPTION = $(if $(FILTER),--filter '$(FILTER)',)

simulink-sldv:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py sldv $(RESULTS)/sldv.jsonl --matlab '$(MATLAB)' --timeout $(TIMEOUT) $(FILTER_OPTION)

simulink-cocosim:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py cocosim $(RESULTS)/cocosim.jsonl --matlab '$(MATLAB)' --kind2 '$(COCOSIM_KIND2)' \
		--z3 '$(Z3)' --timeout $(TIMEOUT) $(COCOSIM_FALLBACK) $(FILTER_OPTION)

simulink-kind2:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py kind2 $(RESULTS)/kind2-2019.jsonl --kind2 '$(COCOSIM_KIND2)' --z3 '$(Z3)' \
		--timeout $(TIMEOUT) $(COCOSIM_FALLBACK) $(FILTER_OPTION)

simulink-run:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py run $(RESULTS)/simulink.jsonl --modes $(MODES) --timeout $(TIMEOUT) \
		$(FILTER_OPTION)

simulink-reported:
	@mkdir -p $(RESULTS)
	$(PYTHON) simulink/harness.py reported $(RESULTS)/reported.json

simulink-report:
	$(PYTHON) simulink/harness.py report $(wildcard $(RESULTS)/simulink.jsonl $(RESULTS)/sldv.jsonl \
		$(RESULTS)/cocosim.jsonl $(RESULTS)/kind2-2019.jsonl) --reported $(RESULTS)/reported.json \
		--out $(RESULTS)/simulink.md

lustre: lustre-run lustre-report

lustre-run:
	@mkdir -p $(RESULTS)
	$(PYTHON) lustre/harness.py run $(RESULTS)/lustre.jsonl --tools $(LUSTRE_TOOLS) --timeout $(LUSTRE_TIMEOUT) \
		$(if $(FILTER),--filter '$(FILTER)',)

lustre-report:
	$(PYTHON) lustre/harness.py report $(RESULTS)/lustre.jsonl --expected lustre/kind2-benchmarks.txt
