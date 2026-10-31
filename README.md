# intrepid-benchmarks

The benchmarks of [intrepid](https://github.com/formalmethods/intrepyd), the
model checker behind intrepyd, with everything needed to run them again on
your own machine:

- **Simulink**: intrepid against Simulink Design Verifier (SLDV) and CoCoSim
  on the Simulink designs of the Lockheed Martin cyber-physical challenges,
  as NASA formalized them;
- **Lustre**: intrepid against [Kind2](https://kind2-mc.github.io/kind2/) on
  the Kind2 benchmark suite.

Nothing here needs a commercial license: intrepyd runs under its
non-commercial license, which comes with it.

## Quick start

On Linux, with Python 3.11 or newer and git:

```bash
make setup      # .venv, with intrepyd (and its Simulink translator) and matplotlib
make check      # the tests of the harnesses
make simulink   # the Simulink benchmark: results/simulink.md
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

**SLDV and CoCoSim** are not run, for now: they need MATLAB. Their results
are the ones the suite reports, read by `simulink/harness.py reported`:

- SLDV's reports, made in 2019 with rational arithmetic, each with its own
  time limit (from 300 to 7200 s). Where a model has several reports, the
  latest one is taken, and an earlier one that said otherwise is noted.
- Kind2's results on CoCoSim's Lustre of the `CoCoSim` models. Those
  properties are named after the requirements (`AP_minus_003A`), not after
  the SLDV models' blocks (`Assertion3`), so the report gives them per
  design, beside the per-property comparison.

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

The variables:

- `TIMEOUT=300`: the seconds per property;
- `MODES=float,real,float-finite,float-free`: the modes;
- `FILTER=euler`: only the models whose path matches a regular expression;
- `RESULTS=elsewhere`: the folder of the results.

`simulink-run` appends to `results/simulink.jsonl` as it goes, and skips the
models and modes it has. An interrupted run goes on where it stopped; to run
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
| `simulink/harness.py` | The Simulink benchmark: `fetch`, `run`, `reported`, `report` |
| `lustre/harness.py`, `lustre/run_one.py` | The Lustre benchmark |
| `lustre/kind2-benchmarks/` | The Kind2 benchmark suite |
| `tests/` | The tests of the harnesses: `make check` |
| `third_party/` | The fetched suites (not committed) |
| `results/` | The results of the runs (not committed) |
