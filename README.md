# intrepid-benchmarks

The benchmarks of [intrepid](https://github.com/formalmethods/intrepyd), the
model checker behind intrepyd, with everything needed to run them again on
your own machine:

- **Simulink**: intrepid against Simulink Design Verifier (SLDV) and CoCoSim
  on the Simulink designs of the Lockheed Martin cyber-physical challenges,
  as NASA formalized them;
- **Lustre**: intrepid against [Kind2](https://kind2-mc.github.io/kind2/) on
  the Kind2 benchmark suite.

intrepid's side needs no commercial license: intrepyd runs under its
non-commercial license, which comes with it. Measuring SLDV and CoCoSim needs
MATLAB, Simulink and Simulink Design Verifier, under your own licenses.

## Quick start

On Linux, with Python 3.11 or newer and git:

```bash
make setup      # .venv, with intrepyd (and its Simulink translator) and matplotlib
make check      # the tests of the harnesses
make simulink   # the Simulink benchmark: results/simulink.md
make simulink-tools MATLAB=... KIND2=... COCOSIM_KIND2=... Z3=...   # SLDV, CoCoSim and Kind2, measured
make simulink-all MATLAB=... KIND2=... COCOSIM_KIND2=... Z3=...     # all of it: intrepid and the other tools
make lustre     # the Lustre benchmark, intrepid alone: its report on the terminal
```

`make setup INTREPYD=/path/to/intrepyd.whl` installs a given build of
intrepyd instead of the one on PyPI. Any other environment with intrepyd
1.0.1 or newer works too: `make simulink PYTHON=/path/to/python`.

## Simulink: intrepid, SLDV and CoCoSim

### The designs

The [Lockheed Martin challenges](https://github.com/hbourbouh/lm_challenges)
are ten flight control problems that Lockheed Martin published in 2016, with
their requirements in natural language. NASA formalized the requirements
with FRET, and checked them with CoCoSim (and Kind2) and with SLDV ("The Ten
Lockheed Martin Cyber-Physical Challenges: Formalized, Analyzed, and
Explained", RE 2020). Its folder `FRET_CoCoSim/` has, for each problem:

- `SLDV/*_SLDV.slx`: the model with its requirements as Assertion and Proof
  Objective blocks, as SLDV checks them, and `SLDV/sldv_output/`, SLDV's
  reports;
- `CoCoSim/`: the model with its requirements as contracts, and
  `cocosim_output/`, CoCoSim's Lustre and Kind2's results on it.

`make fetch` clones the repository at the commit the benchmark is made for
(`133447a`), into `third_party/lm_challenges`. The repository has no license
file and says "All rights reserved": the models are not copied here, and
stay in that checkout on your machine.

### What runs

**intrepid** runs on the SLDV models, so that its verdicts compare with
SLDV's property by property. intrepyd's Simulink translator turns each model
into a circuit, its Assertion and Proof Objective blocks into targets
(reached when they fail), its Assumption blocks into assumptions. Each
target is then checked alone by intrepyd's portfolio, which runs BMC,
k-induction, backward reachability and PDR in parallel, within `TIMEOUT`
seconds (120 by default). Each model runs in a process of its own, once per
mode (`MODES`, by default `float,real`):

| Mode | Singles and doubles | The inputs |
| ---- | ------------------- | ---------- |
| `float` | IEEE floats, exactly | within the design ranges of their Inports |
| `real` | reals, as SLDV's rational arithmetic | within the design ranges of their Inports |
| `float-finite` | IEEE floats | finite: no NaN, no infinity |
| `float-free` | IEEE floats | anything, NaN included |

The design ranges are the `OutMin` and `OutMax` of the root Inports, and of
the Inports of subsystems an input enters as it is: SLDV takes them as
constraints on the inputs, and so does intrepid in these modes. With floats,
a property may fail where it holds of rationals, through rounding, overflow
or NaN. With the two last modes, the report shows how much the design
ranges matter.

sin, cos, tan, asin, acos, atan, atan2, exp, log and log10 are abstracted:
each is an input tied to its argument by true facts of the function. A proof
holds of the model. A counterexample is replayed with the functions
themselves, and is reported as `falsified, not confirmed` if the target is
not reached then.

A model the translator does not support counts its properties as
`unsupported`, with the reason in the report.

Each engine may use `MEMORY` gigabytes (8 by default, an intrepyd newer than
1.0.1): beyond them it stops, and a property whose engines all stopped so is
an `error`, out of memory, rather than `undecided`. With floats, z3 can take
gigabytes in seconds on the larger models.

**intrepid's counterexamples are replayed in Simulink** (`make
simulink-replay MATLAB=...`): the inputs of each counterexample, step by step,
given to the SLDV model simulated in MATLAB, its Assertion blocks not stopping
it; the report says at which step the property's block sees false there, or
that it never does. A counterexample found again where the results lack one.
With floats, Simulink computes as IEEE does, and confirms intrepid; with reals,
the values of a counterexample are rounded to doubles, and it may
legitimately not fail. An input that is NaN or infinite at a step is given
as such. The report's disagreements (a property valid by one tool or mode,
falsified by another) say for each whether intrepid's counterexample fails in
Simulink too: then the property fails in the model as Simulink computes it,
not in the translation alone.

**SLDV and CoCoSim** run on the same SLDV models, on your machine, if you
have MATLAB with Simulink Design Verifier (`make simulink-tools`):

- `sldv`: SLDV's property proving, as the suite's reports were made
  (`simulink/matlab/sldv_prove.m`).
- `cocosim`: [CoCoSim](https://github.com/NASA-SW-VnV/CoCoSim) translates
  the model to Lustre (`simulink/matlab/cocosim_lustre.m`), and
  [Kind2](https://kind2-mc.github.io/kind2/) checks its Assertion and Proof
  Objective blocks, with the options of CoCoSim's verification. CoCoSim's
  mapping gives each property its block, so the three tools compare property
  by property.
- `kind2-2019`, which needs no MATLAB: Kind2 on the Lustre that CoCoSim made
  of the suite's CoCoSim models in 2019 (`FRET_CoCoSim/*/CoCoSim/cocosim_output`),
  where the requirements are contracts, as in NASA's paper. A requirement is
  matched to the block of the SLDV model named after it (`AP_minus_003A`, of
  the contract, to the `AP-003A` that feeds `Assertion5`); those without one
  are listed apart.

SLDV and Kind2 have a time limit for the analysis of a whole model, not per
property: they are given `TIMEOUT` times the number of properties of the
model, what intrepid could take at most on it.

`make fetch-cocosim` clones CoCoSim at a fixed commit into
`third_party/cocosim`, and sets it up as its installer
(`install_cocosim_lib.m`) does, with the two repositories it takes files
from, also at fixed commits; CoCoSim's model checkers are not built, the
runs give it `KIND2` and `Z3`. Give `cocosim` the Kind2 that CoCoSim's
installer takes, v1.7.0 (`scripts/install_tools`): Kind2 v2 and newer
reject the Lustre library CoCoSim adds (its math nodes only assert on their
outputs), Kind2 before v1.7.0 lacks the `--only_parse` of its syntax check.
CoCoSim disables Kind2's IC3IA engine, which v1.7.0 does not have yet: the
option is then left out. Kind2 v1.7.0 rejects the Lustre of some models
that v3 takes ("Width lengths of equation are not equal"): with
`COCOSIM_KIND2` different from `KIND2`, those are retried with `KIND2`, and
the report names them. The same goes for `kind2-2019`, CoCoSim's Lustre
too. CoCoSim's library abstracts some functions, only
bounding their results (sin and cos in [-1, 1], sqrt, a product of reals):
a counterexample of Kind2 through one of them is "falsified, not
confirmed". The models of the suite link to CoCoSim's
library, so SLDV needs it too. MATLAB runs without its desktop, but needs a
display: some of the models do not load with `-nodisplay`. CoCoSim is
tested with R2017b and newer; the runs here were made with R2018a.

The results the suite reports are read too (`simulink/harness.py reported`):

- SLDV's reports, made in 2019 with rational arithmetic, each with its own
  time limit (from 300 to 7200 s). Where a model has several reports, the
  latest one is taken, and an earlier one that said otherwise is noted.
- Kind2's results on CoCoSim's Lustre of the `CoCoSim` models, per design.

These results come from another machine and other versions, years ago: their
times are not comparable with the measured ones. The report says which
results are measured and which are reported, and the plots that mix the two
say so.

### Step by step

```bash
make fetch                 # third_party/lm_challenges at 133447a
make simulink-run          # results/simulink.jsonl, a line per property and mode
make simulink-reported     # results/reported.json, SLDV's and Kind2's results
make simulink-report       # results/simulink.md, and its plots with matplotlib
```

and, for the other tools:

```bash
make fetch-cocosim                                   # third_party/cocosim
make simulink-kind2 COCOSIM_KIND2=/path/to/kind2-v1.7.0 KIND2=/path/to/kind2 Z3=/path/to/z3   # results/kind2-2019.jsonl
make simulink-sldv MATLAB=/path/to/matlab                       # results/sldv.jsonl
make simulink-cocosim MATLAB=/path/to/matlab COCOSIM_KIND2=/path/to/kind2-v1.7.0 Z3=...   # results/cocosim.jsonl
make simulink-replay MATLAB=/path/to/matlab                     # results/replays.jsonl
make simulink-report                                 # all of them in results/simulink.md
```

`MATLAB` may be a script that starts MATLAB (with the environment your
system needs for it): it is given MATLAB's options.

The variables:

- `TIMEOUT=300`: the seconds per property;
- `MEMORY=16`: the gigabytes each engine of intrepid may use;
- `MODES=float,real,float-finite,float-free`: the modes;
- `FILTER=euler`: only the models whose path matches a regular expression;
- `RESULTS=elsewhere`: the folder of the results.

`simulink-run` (and each of the other tools, to its file) appends to
`results/simulink.jsonl` as it goes, and skips the models and modes it has. An interrupted run goes on where it stopped; to run
again from scratch, remove the file. Its first line records the machine (CPU,
memory, system) and the versions of Python, intrepyd and the translator.

`simulink/harness.py` takes the same steps directly. `--help` gives the
details.

## Lustre: intrepid and Kind2

`lustre/` runs intrepyd's engines, and Kind2, on the 848 models of the Kind2
benchmark suite (`lustre/kind2-benchmarks`, verdicts in
`lustre/kind2-benchmarks.txt`):

```bash
make lustre                                   # intrepyd's portfolio, 30 s per model
make lustre LUSTRE_TOOLS=portfolio,kind2 KIND2=/path/to/kind2
```

`lustre/harness.py` takes these and more:

- intrepyd's single engines and Kind2's (`--tools`);
- the encoding of `int` (`--int-type int32|int`; Kind2's is `int`);
- the CPUs and the memory of each run;
- the comparison of two runs (`compare`) and the plots (`plot`).

`lustre/run_one.py` runs one model under one engine. Both document their
options in their first lines. Results of a 5-second run of 2021 are in
`lustre/results_5_seconds/`.

The `taskset` command (util-linux) binds each run to its CPUs. The Kind2
tools need the `kind2` executable, and a z3 for it (`--z3`), at best the
version intrepid is built with.

## Layout

| Path | What |
| ---- | ---- |
| `simulink/harness.py` | The Simulink benchmark: `fetch`, `run`, `sldv`, `cocosim`, `kind2`, `replay`, `reported`, `report` |
| `simulink/matlab.py`, `simulink/matlab/` | CoCoSim's setup, and the MATLAB scripts of the SLDV and CoCoSim runs and of the replays |
| `lustre/harness.py`, `lustre/run_one.py` | The Lustre benchmark |
| `lustre/kind2-benchmarks/` | The Kind2 benchmark suite |
| `tests/` | The tests of the harnesses: `make check` |
| `third_party/` | The fetched suites and CoCoSim (not committed) |
| `results/` | The results of the runs (not committed) |

## License

The harnesses, their tests and this documentation are under the MIT license
([LICENSE](LICENSE)). `lustre/kind2-benchmarks/` is the Kind2 benchmark
suite, under the terms of its authors. The suites fetched into
`third_party/` (the Lockheed Martin challenges, CoCoSim) are not part of this
repository and keep their own terms. intrepyd, which the harnesses run, has
its own license, with a free non-commercial one.
