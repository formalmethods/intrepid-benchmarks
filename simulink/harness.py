"""
The Simulink benchmark: intrepid against Simulink Design Verifier (SLDV) and
CoCoSim on the designs of the Lockheed Martin cyber-physical challenges, as
NASA formalized them (https://github.com/hbourbouh/lm_challenges).

    python simulink/harness.py fetch [--into third_party/lm_challenges]
    python simulink/harness.py fetch-cocosim [--into third_party/cocosim]
    python simulink/harness.py run OUT.jsonl [--modes float,real] [--timeout 120]
        [--filter REGEX] [--suite PATH]
    python simulink/harness.py sldv OUT.jsonl --matlab MATLAB [--timeout 120] ...
    python simulink/harness.py cocosim OUT.jsonl --matlab MATLAB --kind2 KIND2
        --z3 Z3 [--timeout 120] ...
    python simulink/harness.py kind2 OUT.jsonl --kind2 KIND2 --z3 Z3
        [--timeout 120] ...
    python simulink/harness.py reported OUT.json [--suite PATH]
    python simulink/harness.py report OUT.jsonl... --reported REPORTED.json
        [--out results/report.md]

fetch clones the challenges at the commit the benchmark is made for. The
models are not copied anywhere: they stay in that checkout, on the machine
that runs the benchmark (CoCoSim works on a copy of a model in a temporary
folder, removed after). fetch-cocosim clones CoCoSim, and sets it up as its
installer does (simulink/matlab.py).

run translates each SLDV model of the suite (FRET_CoCoSim/*/SLDV) with
intrepyd's Simulink translator, once per mode, and checks each of its
targets (the Assertion and Proof Objective blocks, reached when they fail)
alone with intrepyd's portfolio, within --timeout seconds. Each model and
mode runs in a process of its own. Each result is appended to OUT as a line
of JSON as soon as it is known, and a run started again with the same OUT
skips the models and modes it already has. The first line says on which
machine, with which versions.

The modes (see MODES): float, singles and doubles as IEEE floats, exactly;
real, as reals, which is what SLDV's rational arithmetic does; both with the
inputs within the design ranges of their Inports, as SLDV takes them.
float-finite and float-free are floats without the design ranges, with the
inputs only finite, or anything, NaN included.

sldv, cocosim and kind2 measure the other tools on this machine, on the same
SLDV models, one model at a time, each into its OUT (and resumable as run):

- sldv: SLDV's property proving, with MATLAB (simulink/matlab/sldv_prove.m);
- cocosim: CoCoSim's Lustre of the model (simulink/matlab/cocosim_lustre.m),
  then Kind2 on it, with the options of CoCoSim's verification; CoCoSim's
  mapping names each property after its Assertion or Proof Objective block;
- kind2: Kind2 on the Lustre that CoCoSim made in 2019 of the CoCoSim models
  of the suite (FRET_CoCoSim/*/CoCoSim/cocosim_output), as the suite's
  results were made, without MATLAB. Their properties are the requirements
  of contracts: each is given the block of the SLDV model it names, when
  one does (requirement_key).

SLDV and Kind2 have a time limit for the whole analysis of a model: they are
given --timeout times the number of properties of the model, what intrepid
would take at most.

reported reads the results that the suite reports: SLDV's reports
(FRET_CoCoSim/*/SLDV/sldv_output/*/*_report*.html, from 2019) and Kind2's on
CoCoSim's Lustre (FRET_CoCoSim/*/CoCoSim/cocosim_output/**/*.xml). They were
made on another machine, with versions of their own: their times are not
comparable with the measured ones, and the report never mixes them without
saying so.

report writes, in Markdown, per model and property the verdict and time of
each tool and mode, the totals, the disagreements, Kind2's results per
design, and, with matplotlib, the plots next to it.
"""

import argparse
import datetime
import fractions
import functools
import glob
import html.parser
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile

import matlab

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUITE = os.path.join(ROOT, 'third_party', 'lm_challenges')
LM_URL = 'https://github.com/hbourbouh/lm_challenges.git'
# "export Models to R2017b", 2020-02-11
LM_COMMIT = '133447a8943721eaa1d16acf1ba5845b67a98df1'

# mode -> the options of intrepyd.simulink.translate_file
MODES = {
    'float': {'realtype': 'float', 'design_ranges': True},
    'real': {'realtype': 'real', 'design_ranges': True},
    'float-finite': {'realtype': 'float', 'finite_inputs': True},
    'float-free': {'realtype': 'float'},
}
DEFAULT_MODES = ('float', 'real')
MODE_TITLES = {
    'float': 'intrepid, floats',
    'real': 'intrepid, reals',
    'float-finite': 'intrepid, floats, finite inputs, no ranges',
    'float-free': 'intrepid, floats, no constraint on the inputs',
}
# the other tools, measured here: their mode -> title
TOOL_TITLES = {
    'sldv': 'SLDV, measured',
    'cocosim': 'CoCoSim and Kind2, measured',
    'kind2-2019': "Kind2 on CoCoSim's Lustre of 2019, measured",
}
MODE_TITLES.update(TOOL_TITLES)
# The verdicts, in the order of the report's totals
VERDICTS = ('valid', 'valid under approximation', 'falsified', 'falsified, not confirmed',
            'undecided', 'unsupported', 'error')


# The suite

def fetch(into):
    """Clones the challenges at LM_COMMIT into into, or checks that it is there"""
    if os.path.isdir(os.path.join(into, '.git')):
        head = subprocess.run(['git', '-C', into, 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True).stdout.strip()
        if head == LM_COMMIT:
            print(f'{into}: at {LM_COMMIT[:7]} already')
            return
        raise SystemExit(f'{into} is at {head[:7]}, not {LM_COMMIT[:7]}: remove it, or check out {LM_COMMIT}')
    os.makedirs(into, exist_ok=True)
    for command in (['init', '-q'], ['remote', 'add', 'origin', LM_URL],
                    ['fetch', '-q', '--depth', '1', 'origin', LM_COMMIT],
                    ['checkout', '-q', 'FETCH_HEAD']):
        subprocess.run(['git', '-C', into] + command, check=True)
    print(f'{into}: {LM_URL} at {LM_COMMIT[:7]}')


def models(suite):
    """The SLDV models of the suite, relative to it, in order"""
    found = glob.glob(os.path.join(suite, 'FRET_CoCoSim', '*', 'SLDV', '*.slx'))
    found += glob.glob(os.path.join(suite, 'FRET_CoCoSim', '*', 'SLDV', '*.mdl'))
    return sorted(os.path.relpath(path, suite) for path in found)


def model_name(model):
    return os.path.splitext(os.path.basename(model))[0]


def design_of(model):
    """The problem a model belongs to: 7_autopilot of FRET_CoCoSim/7_autopilot/SLDV/..."""
    return model.split(os.sep)[1] if os.sep in model else model.split('/')[1]


def data_files(suite, model):
    """The MAT files of a model's problem, which its parameters refer to"""
    return sorted(glob.glob(os.path.join(suite, 'FRET_CoCoSim', design_of(model), '*_data.mat')))[:1]


# Running intrepid

def machine():
    """What the results were measured on, and with what"""
    info = {'kind': 'machine', 'date': datetime.datetime.now().isoformat(timespec='seconds'),
            'platform': platform.platform(), 'python': platform.python_version(),
            'cpu': platform.processor() or platform.machine(), 'cores': os.cpu_count()}
    try:
        with open('/proc/cpuinfo', encoding='utf-8') as stream:
            names = re.findall(r'^model name\s*:\s*(.+)$', stream.read(), re.M)
        if names:
            info['cpu'] = names[0]
    except OSError:
        pass
    try:
        with open('/proc/meminfo', encoding='utf-8') as stream:
            kilobytes = int(re.search(r'MemTotal:\s*(\d+)', stream.read()).group(1))
        info['memory_gb'] = round(kilobytes / 1024 / 1024, 1)
    except (OSError, AttributeError):
        pass
    try:
        import intrepyd  # pylint: disable=import-outside-toplevel
        # a checkout has its version next to the package; an installation, in
        # its metadata
        version = os.path.join(os.path.dirname(os.path.dirname(intrepyd.__file__)), 'VERSION')
        if os.path.exists(version):
            with open(version, encoding='utf-8') as stream:
                info['intrepyd'] = stream.read().strip() + ' (a checkout)'
        else:
            info['intrepyd'] = importlib.metadata.version('intrepyd')
    except (ImportError, importlib.metadata.PackageNotFoundError) as error:
        info['intrepyd'] = f'not available: {error}'
    try:
        from intrepyd import simulink  # pylint: disable=import-outside-toplevel
        info['translator'] = simulink.version()
        info['license'] = (simulink.license_info() or {}).get('kind', 'unknown')
    except Exception as error:  # pylint: disable=broad-except
        info['translator'] = f'not available: {error}'
    if os.environ.get('INTREPID_LIBRARY'):
        info['intrepid_library'] = os.environ['INTREPID_LIBRARY']
    return info


def check_model(suite, model, mode, timeout, emit, properties=None, memory=None):
    """Translates a model in a mode and checks each target (of properties,
    if given); emit(record) for each, with the inputs of the counterexample
    of a property falsified; memory, if given, the gigabytes each engine
    may use"""
    # Imported here: only the process that checks needs the native libraries
    import runpy  # pylint: disable=import-outside-toplevel
    import intrepyd  # pylint: disable=import-outside-toplevel
    from intrepyd import simulink  # pylint: disable=import-outside-toplevel
    from intrepyd.engine import EngineResult  # pylint: disable=import-outside-toplevel
    base = {'kind': 'result', 'tool': 'intrepid', 'mode': mode, 'model': model_name(model),
            'design': design_of(model)}
    try:
        python = simulink.translate_file(os.path.join(suite, model), mats=data_files(suite, model),
                                         **MODES[mode])
    except simulink.SimulinkError as error:
        emit(dict(base, property=None, verdict='unsupported', error=str(error)))
        return
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'circuit.py')
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(python)
        built = runpy.run_path(path)
    ctx = intrepyd.Context()
    circuit = built['mk_instance'](ctx, 'top')
    circuit.mk_circuit()
    abstractions = getattr(circuit, 'abstractions', {})
    simulink_types = built.get('SIMULINK_TYPES', {})
    for name, target in circuit.targets.items():
        if properties is not None and block_in_model(name, model_name(model)) not in properties:
            continue
        try:
            portfolio = (ctx.mk_portfolio() if memory is None
                         else ctx.mk_portfolio(memory_limit=int(memory * 1024 ** 3)))
        except TypeError:
            # an intrepyd without memory limits (1.0.1)
            portfolio = ctx.mk_portfolio()
        portfolio.add_target(target)
        record = dict(base, property=block_in_model(name, model_name(model)))
        try:
            result = portfolio.reach_targets(timeout=timeout)
        except Exception as error:  # pylint: disable=broad-except
            emit(dict(record, verdict='error', error=str(error)))
            continue
        record['time'] = round(portfolio.get_last_time(), 3)
        record['engine'] = portfolio.get_last_engine()
        if result == EngineResult.UNREACHABLE:
            record['verdict'] = 'valid'
        elif result == EngineResult.REACHABLE:
            record['verdict'] = 'falsified'
            record['counterexample'] = counterexample(portfolio.get_last_trace(), circuit, simulink_types)
            if abstractions:
                # through a function abstracted as an input, the
                # counterexample may be the abstraction's: replayed with the
                # functions themselves
                reached, _ = simulink.confirm_counterexample(ctx, circuit, portfolio.get_last_trace(), target)
                if not reached:
                    record['verdict'] = 'falsified, not confirmed'
        else:
            record['verdict'] = 'undecided'
        errors = portfolio.get_last_errors()
        if errors:
            record['engine_errors'] = errors
            if record['verdict'] == 'undecided' and set(errors) >= set(portfolio.engines):
                # no engine ran to the end: not an answer
                record.update(verdict='error', error='; '.join(f'{e}: {m}' for e, m in sorted(errors.items())))
            out_of_memory = getattr(portfolio, 'get_last_out_of_memory', lambda: ())()
            if out_of_memory:
                record['out_of_memory'] = list(out_of_memory)
        emit(record)


def trace_number(text):
    """A value of an intrepyd trace as a python number: -3.0 is (- 3.0), a
    rational (/ 1 3) or 1/3, a boolean T or F; None if it has none"""
    text = text.strip()
    if text.startswith('(- ') and text.endswith(')'):
        value = trace_number(text[3:-1])
        return None if value is None else -value
    if text.startswith('(/ ') and text.endswith(')'):
        parts = text[3:-1].split()
        if len(parts) == 2:
            a, b = trace_number(parts[0]), trace_number(parts[1])
            return None if a is None or b is None else float(fractions.Fraction(a) / fractions.Fraction(b))
    if text in ('T', 'true'):
        return 1
    if text in ('F', 'false'):
        return 0
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(fractions.Fraction(text))
    except (ValueError, ZeroDivisionError):
        pass
    try:
        return float(text)
    except ValueError:
        return None


def counterexample(trace, circuit, simulink_types):
    """The values of the root Inports at each step of a trace, by name (a
    vector's elements as name[i]); its inputs of the translation alone, an
    abstraction's, left out. A value the trace leaves free (?) is 0, and
    its input listed as free. A Simulink Inport takes finite values only: an
    infinite or NaN value is 0 in inputs, and in specials, by name, a code
    at each step: 0 the value of inputs, 1 +inf, 2 -inf, 3 NaN. Not
    replayable if a value is not a number at all"""
    steps = trace.get_max_depth() + 1
    inputs = {}
    specials = {}
    free = []
    replayable = True
    for name, net in circuit.inputs.items():
        if name not in simulink_types:
            continue
        values = []
        codes = []
        for step in range(steps):
            text = trace.get_value(net, step)
            value = trace_number(text)
            code = 0
            if text.strip() == '?':
                value = 0
                if name not in free:
                    free.append(name)
            elif value is None:
                replayable = False
            elif isinstance(value, float) and not math.isfinite(value):
                code = 3 if math.isnan(value) else (1 if value > 0 else 2)
                value = 0
            values.append(value)
            codes.append(code)
        inputs[name] = values
        if any(codes):
            specials[name] = codes
    example = {'steps': steps, 'inputs': inputs, 'replayable': replayable}
    if specials:
        example['specials'] = specials
    if free:
        example['free'] = free
    return example


def block_in_model(path, model):
    """The path of a block without its model's name, as SLDV gives it: the
    model's name may have a suffix (NLGuidance's root is its name and _tmp)"""
    top, _, rest = path.partition('/')
    return rest if rest and top.startswith(model) else path


def read_jsonl(path):
    records = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as stream:
            for line in stream:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def run_models(arguments, modes, info, check):
    """For each model of the suite (--filter's) and mode not in OUT yet,
    check(suite, model, mode, emit), which emit(record)s each result and says
    whether the model is done. OUT is appended to as results come; its first
    line is info(), on what and with what they were measured."""
    suite = os.path.abspath(arguments.suite)
    if not os.path.isdir(os.path.join(suite, 'FRET_CoCoSim')):
        raise SystemExit(f'no suite in {suite}: run "python simulink/harness.py fetch" first')
    out_path = os.path.abspath(arguments.out)
    done = {(r['model'], r['mode']) for r in read_jsonl(out_path) if r.get('kind') == 'done'}
    with open(out_path, 'a', encoding='utf-8') as out:
        if not read_jsonl(out_path):
            record = info()
            record.update(timeout=arguments.timeout, suite_commit=LM_COMMIT)
            if getattr(arguments, 'memory', None):
                record['memory_per_engine_gb'] = arguments.memory
            out.write(json.dumps(record) + '\n')
            out.flush()

        def emit(record):
            out.write(json.dumps(record) + '\n')
            out.flush()
            print(f'  {record.get("property")}: {record["verdict"]}'
                  + (f' in {record["time"]} s' if record.get('time') is not None else ''), flush=True)

        for model in models(suite):
            if arguments.filter and not re.search(arguments.filter, model):
                continue
            for mode in modes:
                if (model_name(model), mode) in done:
                    continue
                print(f'{model_name(model)} ({mode})', flush=True)
                if check(suite, model, mode, emit):
                    out.write(json.dumps({'kind': 'done', 'model': model_name(model), 'mode': mode}) + '\n')
                    out.flush()
                else:
                    print(f'  {model_name(model)} ({mode}) failed: run it again to retry it', flush=True)


def run(arguments):
    """Runs intrepid on every model in every mode not in OUT yet, each in a
    process of its own"""
    modes = arguments.modes.split(',')
    for mode in modes:
        if mode not in MODES:
            raise SystemExit(f'unknown mode {mode}: {", ".join(MODES)}')

    def check(suite, model, mode, emit):
        command = [sys.executable, os.path.abspath(__file__), 'check', model, mode,
                   '--timeout', str(arguments.timeout), '--suite', suite, '--memory', str(arguments.memory)]
        # in a folder of its own; intrepid writes trace.cpp where it meets an
        # error, which is kept, next to the results, to find why
        scratch = tempfile.mkdtemp(prefix='simulink-check-')
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, cwd=scratch) as process:
            for line in process.stdout:
                if line.startswith('{'):
                    emit(json.loads(line))
            status = process.wait()
        trace = os.path.join(scratch, 'trace.cpp')
        if os.path.exists(trace):
            kept = os.path.join(os.path.dirname(os.path.abspath(arguments.out)),
                                f'trace-{model_name(model)}-{mode}.cpp')
            shutil.copyfile(trace, kept)
            print(f'  intrepid met an error and wrote {kept}', flush=True)
        if status != 0:
            print(f'  the check exited with status {status}', flush=True)
        shutil.rmtree(scratch, ignore_errors=True)
        return status == 0

    run_models(arguments, modes, machine, check)


# Replaying intrepid's counterexamples in Simulink

def replay(arguments):
    """Simulates in Simulink the counterexamples of intrepid's falsified
    properties (found again, where the results lack them), and appends to
    OUT whether each one's property fails there too: a record per
    counterexample, of kind 'replay'"""
    suite = os.path.abspath(arguments.suite)
    paths = {model_name(m): m for m in models(suite)}
    falsified = {}      # model -> [record]
    for path in arguments.runs:
        for record in read_jsonl(path):
            if (record.get('kind') == 'result' and record.get('tool') == 'intrepid'
                    and record.get('verdict') in ('falsified', 'falsified, not confirmed')
                    and record['model'] in paths
                    and not (arguments.filter and not re.search(arguments.filter, paths[record['model']]))):
                falsified.setdefault(record['model'], []).append(record)
    done = {(r['model'], r['mode'], r['property']) for r in read_jsonl(arguments.out) if r.get('kind') == 'replay'}
    with open(arguments.out, 'a', encoding='utf-8') as out:
        for model, records in sorted(falsified.items()):
            records = [r for r in records if (model, r['mode'], r['property']) not in done]
            if not records:
                continue
            print(f'{model}: {len(records)} counterexamples', flush=True)
            records = with_counterexamples(suite, paths[model], records, arguments.timeout)
            for record in replay_model(arguments, suite, paths[model], records):
                out.write(json.dumps(record) + '\n')
                out.flush()
                print(f'  {record["property"]} ({record["mode"]}): {record["verdict"]}'
                      + (f': {record["error"]}' if record.get('error') else ''), flush=True)


def with_counterexamples(suite, model, records, timeout):
    """The records, those without a counterexample with one found again by
    intrepid (one process per mode); a record whose property is not
    falsified again keeps none"""
    found = {}
    for mode in sorted({r['mode'] for r in records if not r.get('counterexample')}):
        wanted = [r['property'] for r in records if r['mode'] == mode and not r.get('counterexample')]
        command = [sys.executable, os.path.abspath(__file__), 'check', model, mode, '--timeout', str(timeout),
                   '--suite', suite] + [a for p in wanted for a in ('--property', p)]
        scratch = tempfile.mkdtemp(prefix='simulink-replay-')
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, cwd=scratch) as process:
            for line in process.stdout:
                if line.startswith('{'):
                    again = json.loads(line)
                    found[(mode, again['property'])] = again.get('counterexample')
            process.wait()
        if os.path.exists(os.path.join(scratch, 'trace.cpp')):
            kept = os.path.join(ROOT, 'results', f'trace-replay-{model_name(model)}-{mode}.cpp')
            shutil.copyfile(os.path.join(scratch, 'trace.cpp'), kept)
            print(f'  intrepid met an error and wrote {kept}', flush=True)
        shutil.rmtree(scratch, ignore_errors=True)
    return [dict(r, counterexample=r.get('counterexample') or found.get((r['mode'], r['property'])))
            for r in records]


def first_lines(text, count=2):
    """The first lines of a MATLAB error report, on one line: where, then why"""
    return ' '.join([line.strip() for line in text.strip().splitlines() if line.strip()][:count])


def replay_model(arguments, suite, model, records):
    """The replay records of the counterexamples of one model, simulated in
    one MATLAB session"""
    replays = []
    cases = []
    for i, record in enumerate(records):
        base = {'kind': 'replay', 'tool': 'intrepid', 'mode': record['mode'], 'model': record['model'],
                'property': record['property']}
        example = record.get('counterexample')
        if not example:
            replays.append(dict(base, verdict='not replayed', error='no counterexample found again'))
        elif not example.get('replayable', True):
            replays.append(dict(base, verdict='not replayed', steps=example['steps'],
                                error='a value of the counterexample is not a number'))
        else:
            case = {'id': i, 'property': record['property'], 'steps': example['steps'], 'inputs': example['inputs']}
            if example.get('specials'):
                case['specials'] = example['specials']
            cases.append(case)
            replays.append(dict(base, steps=example['steps']))
    if not cases:
        return replays
    folder = tempfile.mkdtemp(prefix='simulink-replay-')
    cases_path = os.path.join(folder, 'cases.json')
    out = os.path.join(folder, 'replayed.json')
    with open(cases_path, 'w', encoding='utf-8') as stream:
        json.dump(cases, stream)
    mats = '{' + ', '.join(matlab.matlab_string(m) for m in data_files(suite, model)) + '}'
    statement = (f'replay_counterexamples({matlab.matlab_string(arguments.cocosim)}, '
                 f'{matlab.matlab_string(os.path.join(suite, model))}, {mats}, '
                 f'{matlab.matlab_string(cases_path)}, {matlab.matlab_string(out)})')
    matlab.run_matlab(arguments.matlab, statement, folder, 600 + 60 * len(cases), os.path.join(folder, 'matlab.log'))
    try:
        with open(out, encoding='utf-8') as stream:
            result = json.load(stream)
    except (OSError, ValueError):
        try:
            with open(os.path.join(folder, 'matlab.log'), encoding='utf-8', errors='replace') as stream:
                said = [line.strip() for line in stream if line.strip()][-3:]
        except OSError:
            said = []
        result = {'error': 'MATLAB wrote no result' + (': ' + ' / '.join(said) if said else '')}
    by_id = {c['id']: c for c in (result.get('cases') or [])} if isinstance(result.get('cases'), list) else {}
    if isinstance(result.get('cases'), dict):
        by_id = {result['cases']['id']: result['cases']}
    for i, replayed in enumerate(replays):
        if 'verdict' in replayed:
            continue
        case = by_id.get(i)
        if case is None:
            replayed.update(verdict='error', error=first_lines(result.get('error') or 'not simulated'))
        elif case.get('error'):
            replayed.update(verdict='error', error=first_lines(case['error']))
        else:
            replayed['simulink_step'] = case['step']
            replayed['verdict'] = 'confirmed' if case['step'] >= 0 else 'not confirmed'
    shutil.rmtree(folder, ignore_errors=True)
    return replays


# Running SLDV, CoCoSim and Kind2

def _p(element, name):
    for child in element.findall('P'):
        if child.get('Name') == name:
            return child.text
    return None


def property_blocks(suite, model):
    """The Assertion and Proof Objective blocks of an SLX model: their paths
    from the model's root (as intrepid names its targets) -> the names that
    may be the requirement they check, theirs and that of the block feeding
    them. {} for an MDL."""
    if not model.endswith('.slx'):
        return {}
    with zipfile.ZipFile(os.path.join(suite, model)) as archive:
        root = ElementTree.fromstring(archive.read('simulink/blockdiagram.xml'))
    found = {}

    def walk(system, path):
        blocks = {block.get('SID'): block for block in system.findall('Block')}
        for block in system.findall('Block'):
            name = block.get('Name', '')
            is_property = block.get('BlockType') == 'Assertion' or 'Proof Objective' in (
                _p(block, 'SourceBlock') or '')
            if is_property:
                names = [name]
                for line in system.findall('Line'):
                    destinations = [_p(line, 'Dst')] + [_p(branch, 'Dst') for branch in line.iter('Branch')]
                    source = _p(line, 'Src')
                    if source and any(d and d.split('#')[0] == block.get('SID') for d in destinations):
                        feeding = blocks.get(source.split('#')[0])
                        if feeding is not None:
                            names.append(feeding.get('Name', ''))
                found[path + name] = names
            inner = block.find('System')
            if inner is not None:
                walk(inner, path + name + '/')

    walk(root.find('Model/System'), '')
    return found


def requirement_key(name, lustre=False):
    """What a requirement's names have in common: AP-003A, of a block, and
    AP_minus_003A_3098_002, of a guarantee in CoCoSim's Lustre (lustre), both
    ap003a; roll_hold_mode_ensure_3648_001, of a mode, and roll_hold_mode.
    CoCoSim ends the Lustre names with the block's SID"""
    if lustre:
        name = re.sub(r'_\d+_\d+$', '', name)
        name = re.sub(r'_ensure$', '', name).replace('_minus_', '')
    return re.sub(r'[^a-z0-9]', '', name.lower())


def sldv_status_verdict(status):
    """The verdict of an SLDV objective's status, as in its data file"""
    status = status.lower()
    if status == 'valid':
        return 'valid'
    if status.startswith('valid under approximation'):
        return 'valid under approximation'
    if status == 'falsified':
        return 'falsified'
    if status.startswith('falsified'):
        return 'falsified, not confirmed'
    return 'undecided'


def kind2_version(kind2):
    try:
        return subprocess.run([kind2, '--version'], capture_output=True, text=True, check=False,
                              timeout=60).stdout.strip()
    except OSError as error:
        return f'not available: {error}'


@functools.cache
def kind2_engines(kind2):
    """The engines Kind2 can enable (IC3IA came with v1.8.0)"""
    process = subprocess.run([kind2, '--help'], capture_output=True, text=True, check=False)
    match = re.search(r'--enable <string>.*?can be (.*?)\n\s*Enable', process.stdout, re.DOTALL)
    return set(re.findall(r'\w+', match.group(1))) if match else set()


def run_kind2(kind2, z3, lustre, options, seconds):
    """Kind2 on lustre, with z3 as its solver and options, within seconds;
    its properties, with their verdicts and times, its version, and the
    seconds it took"""
    with tempfile.TemporaryDirectory(prefix='kind2-') as folder:
        # CoCoSim disables IC3IA, which a Kind2 before v1.8.0 does not have
        disable = ['--disable', 'IC3IA'] if 'IC3IA' in kind2_engines(kind2) else []
        command = [kind2, '-xml'] + disable + ['--smt_solver', 'Z3', '--z3_bin', z3,
                   '--timeout', str(seconds), '--output_dir', folder] + options + [lustre]
        started = datetime.datetime.now()
        # in a session of its own: stopped, with its solvers
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=folder,
                              start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=seconds + 120)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                return None, f'Kind2 did not end within {seconds + 120} s'
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        took = (datetime.datetime.now() - started).total_seconds()
    try:
        results = parse_kind2_results(stdout)
    except ElementTree.ParseError as error:
        return None, f'Kind2 exited with {process.returncode}: {error}: {stderr.strip()[:500]}'
    if results['errors'] and not results['properties']:
        return None, 'Kind2: ' + '; '.join(results['errors'])
    # the checks of a contract's modes being reachable are not properties
    results['properties'] = [p for p in results['properties']
                             if p['answer'] not in ('reachable', 'unreachable')]
    results['seconds'] = round(took, 3)
    return results, None


LUSTRE_NODE = re.compile(r'\bnode\s+(imported\s+)?(\w+)\s*\([^)]*\)\s*returns\s*\([^)]*\)\s*;'
                         r'(\s*let\b(.*?)\btel\b)?', re.DOTALL)


def abstracted_nodes(lustre):
    """The nodes of the Lustre files lustre includes that only abstract a
    function (imported, or only asserting on their outputs, as CoCoSim's
    sin and cos in [-1, 1]), which lustre calls"""
    with open(lustre, encoding='utf-8', errors='replace') as stream:
        main = re.sub(r'\(\*.*?\*\)|--[^\n]*', '', stream.read(), flags=re.DOTALL)
    abstract = set()
    for included in re.findall(r'\binclude\s+"([^"]+)"', main):
        path = os.path.join(os.path.dirname(lustre), included)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8', errors='replace') as stream:
            text = re.sub(r'\(\*.*?\*\)|--[^\n]*', '', stream.read(), flags=re.DOTALL)
        for match in LUSTRE_NODE.finditer(text):
            body = re.sub(r'\bassert\b[^;]*;', '', match.group(4) or '')
            if match.group(1) or not re.search(r'[^<>=!]=[^=>]', body):
                abstract.add(match.group(2))
    return sorted(name for name in abstract if re.search(r'\b' + name + r'\s*\(', main))


def kind2_records(base, results, names, abstractions=()):
    """The records of Kind2's results: each property under the SLDV block
    names(its last name) gives, or under its own name. Through the
    abstractions, a counterexample may be the abstraction's: not confirmed"""
    records = []
    for prop in results['properties']:
        last = prop['property'].rsplit('.', 1)[-1]
        last = re.sub(r'\[\d+\]$', '', last)
        block = names(last)
        record = dict(base, property=block or last, verdict=prop['verdict'], time=prop['time'],
                      engine=prop['engine'])
        if abstractions and record['verdict'] == 'falsified':
            record.update(verdict='falsified, not confirmed', abstractions=list(abstractions))
        if block is None:
            record['unmatched'] = True
        records.append(record)
    return records


def tools_machine(**tools):
    def info():
        record = machine()
        record.update(tools)
        return record
    return info


def sldv(arguments):
    """Runs SLDV's property proving on every model not in OUT yet"""
    def check(suite, model, mode, emit):
        base = {'kind': 'result', 'tool': 'sldv', 'mode': mode, 'model': model_name(model),
                'design': design_of(model)}
        count = max(1, len(property_blocks(suite, model)))
        folder = tempfile.mkdtemp(prefix='simulink-sldv-')
        out = os.path.join(folder, 'result.json')
        mats = '{' + ', '.join(matlab.matlab_string(m) for m in data_files(suite, model)) + '}'
        statement = (f'sldv_prove({matlab.matlab_string(arguments.cocosim)}, '
                     f'{matlab.matlab_string(os.path.join(suite, model))}, {mats}, {arguments.timeout}, '
                     f'{matlab.matlab_string(out)})')
        # SLDV's own limit is on the analysis; MATLAB is given some more
        status = matlab.run_matlab(arguments.matlab, statement, folder,
                                   arguments.timeout * count * 2 + 600, os.path.join(folder, 'matlab.log'))
        try:
            with open(out, encoding='utf-8') as stream:
                result = json.load(stream)
        except (OSError, ValueError):
            result = {'error': f'MATLAB ended with {status} without a result'}
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if result.get('error') or 'objectives' not in result:
            emit(dict(base, property=None, verdict='error', error=result.get('error', 'no objectives')))
            return status is not None
        root = model_name(model) + '/'
        for objective in result['objectives'] or []:
            block = objective.get('block', '')
            emit(dict(base, property=block[len(root):] if block.startswith(root) else block,
                      verdict=sldv_status_verdict(objective['status']), status=objective['status'],
                      time=objective.get('time'), version=result.get('version'), limit=result.get('limit')))
        return True

    run_models(arguments, ['sldv'], tools_machine(matlab=arguments.matlab), check)


def syntax_errors(folder):
    """The errors of Kind2 in CoCoSim's reports of a failed syntax check, under folder"""
    errors = []
    for path in sorted(glob.glob(os.path.join(folder, '**', '*_error_messages.html'), recursive=True)):
        with open(path, encoding='utf-8', errors='replace') as stream:
            text = stream.read()
        # Kind2's XML output, as it is, in the HTML
        for match in re.finditer(r'<Log class="error"([^>]*)>(.*?)</Log>', text, re.DOTALL):
            where = re.search(r'line="(\d+)" column="(\d+)"(?: file="([^"<]*)")?', match.group(1))
            message = ' '.join(re.sub(r'<[^>]*>', ' ', match.group(2)).split())
            if where:
                # CoCoSim may have made the file a link: then the main Lustre file
                name = os.path.basename(where.group(3)) if where.group(3) else 'the Lustre file'
                message = f'{name}:{where.group(1)}:{where.group(2)}: {message}'
            errors.append(f'Kind2: {message}')
    return '; '.join(dict.fromkeys(errors))


def cocosim(arguments):
    """Runs CoCoSim, then Kind2, on every model not in OUT yet"""
    def check(suite, model, mode, emit):
        base = {'kind': 'result', 'tool': 'cocosim', 'mode': mode, 'model': model_name(model),
                'design': design_of(model)}
        folder = tempfile.mkdtemp(prefix='simulink-cocosim-')
        try:
            # the Kind2 CoCoSim checks its Lustre's syntax with, then the
            # fallback, if the first rejects the Lustre
            errors = []
            for kind2 in [arguments.kind2] + ([arguments.fallback_kind2] if arguments.fallback_kind2 else []):
                result, status, rejected = translate(suite, model, kind2, folder)
                if result.get('failed'):
                    errors.append(f'{kind2_version(kind2)}: {result["error"]}' if rejected else result['error'])
                if not result.get('failed') or not rejected:
                    break
            if result.get('failed'):
                emit(dict(base, property=None, verdict='unsupported', error='; '.join(errors)))
                return status is not None
            properties = result.get('assertions') or []
            if isinstance(properties, str):
                properties = [properties]
            options = ['--lus_main', result['top']]
            if properties:
                options += ['--slice_nodes', 'off', '--check_subproperties', 'true']
            else:
                options += ['--modular', 'true']
            count = max(1, len(properties) or len(property_blocks(suite, model)))
            results, error = run_kind2(kind2, arguments.z3, result['lustre'], options, arguments.timeout * count)
            if error:
                emit(dict(base, property=None, verdict='error', error=error))
                return True
            mapping_path = os.path.join(os.path.dirname(result['lustre']), result['top'] + '_mapping.json')
            origins = {}
            if os.path.exists(mapping_path):
                with open(mapping_path, encoding='utf-8') as stream:
                    for entry in json.load(stream):
                        if 'PropertyName' in entry:
                            origins[entry['PropertyName']] = entry['OriginPath']
            top = result['top'] + '/'

            def names(prop):
                origin = origins.get(prop)
                return origin[len(top):] if origin and origin.startswith(top) else origin

            base.update(translation_time=round(result.get('seconds', 0), 3), kind2=results['version'])
            for record in kind2_records(base, results, names, abstracted_nodes(result['lustre'])):
                emit(record)
            return True
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def translate(suite, model, kind2, parent):
        """CoCoSim's result on model, with kind2 for its syntax check, the
        status of MATLAB, and whether kind2 rejected the Lustre"""
        # CoCoSim writes next to the model: a copy, in a folder of its own
        folder = tempfile.mkdtemp(dir=parent)
        copy = os.path.join(folder, os.path.basename(model))
        shutil.copy2(os.path.join(suite, model), copy)
        out = os.path.join(folder, 'result.json')
        mats = '{' + ', '.join(matlab.matlab_string(m) for m in data_files(suite, model)) + '}'
        statement = (f'cocosim_lustre({matlab.matlab_string(arguments.cocosim)}, '
                     f'{matlab.matlab_string(os.path.abspath(kind2))}, '
                     f'{matlab.matlab_string(os.path.abspath(arguments.z3))}, '
                     f'{matlab.matlab_string(copy)}, {mats}, {matlab.matlab_string(out)})')
        log = os.path.join(folder, 'matlab.log')
        status = matlab.run_matlab(arguments.matlab, statement, folder, 3600, log)
        try:
            with open(out, encoding='utf-8') as stream:
                result = json.load(stream)
        except (OSError, ValueError):
            result = {'failed': True, 'error': f'MATLAB ended with {status} without a result'}
        rejected = syntax_errors(folder)
        if result.get('failed'):
            errors = []
            if os.path.exists(log):
                with open(log, encoding='utf-8', errors='replace') as stream:
                    errors = [line.strip() for line in stream if line.startswith('(Error)')]
            result['error'] = (result.get('error') or rejected or ' '.join(errors[:3]) or 'CoCoSim failed')
        return result, status, bool(rejected)

    run_models(arguments, ['cocosim'],
               tools_machine(matlab=arguments.matlab, cocosim=matlab.COCOSIM_COMMIT,
                             kind2=kind2_version(arguments.kind2), z3=arguments.z3,
                             **({'fallback_kind2': kind2_version(arguments.fallback_kind2)}
                                if arguments.fallback_kind2 else {})), check)


def lustre_2019(suite, model):
    """The Lustre CoCoSim made in 2019 of the CoCoSim model of an SLDV model"""
    name = re.sub(r'_SLDV$', '', model_name(model)) + '_PP.KIND2.lus'
    found = glob.glob(os.path.join(suite, 'FRET_CoCoSim', design_of(model), 'CoCoSim', 'cocosim_output', '**',
                                   name), recursive=True)
    return sorted(found)[0] if found else None


def kind2_2019(arguments):
    """Runs Kind2 on CoCoSim's Lustre of 2019 of every model not in OUT yet"""
    def check(suite, model, mode, emit):
        base = {'kind': 'result', 'tool': 'kind2', 'mode': mode, 'model': model_name(model),
                'design': design_of(model)}
        lustre = lustre_2019(suite, model)
        if lustre is None:
            emit(dict(base, property=None, verdict='unsupported', error='no Lustre of CoCoSim in the suite'))
            return True
        blocks = property_blocks(suite, model)
        keys = {}
        for path, block_names in blocks.items():
            for name in block_names:
                keys.setdefault(requirement_key(name), path)
        with open(lustre, encoding='utf-8') as stream:
            count = max(1, len(blocks) or len(re.findall(r'\bguarantee\b', stream.read())))
        # the suite's results were made with contracts: --modular, as CoCoSim;
        # the fallback Kind2, if the first rejects the Lustre
        errors = []
        for kind2 in [arguments.kind2] + ([arguments.fallback_kind2] if arguments.fallback_kind2 else []):
            results, error = run_kind2(kind2, arguments.z3, lustre, ['--modular', 'true'],
                                       arguments.timeout * count)
            if not error:
                break
            errors.append(f'{kind2_version(kind2)}: {error}')
            if not error.startswith('Kind2: '):
                break
        if error:
            error = '; '.join(errors)
            emit(dict(base, property=None, verdict='error', error=error))
            return True
        base['kind2'] = results['version']
        for record in kind2_records(base, results, lambda prop: keys.get(requirement_key(prop, lustre=True)),
                                    abstracted_nodes(lustre)):
            emit(record)
        return True

    run_models(arguments, ['kind2-2019'],
               tools_machine(kind2=kind2_version(arguments.kind2), z3=arguments.z3,
                             **({'fallback_kind2': kind2_version(arguments.fallback_kind2)}
                                if arguments.fallback_kind2 else {})), check)


# The reported results

class _ReportParser(html.parser.HTMLParser):
    """The rows of the tables of an SLDV report, each with the title of the
    section it is in ("Objectives Valid", ...), and the report's text"""

    def __init__(self):
        super().__init__()
        self.section = None
        self.rows = []
        self.text = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'div' and attributes.get('class') == 'sect1':
            self.section = attributes.get('title')
        elif tag == 'tr':
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None:
            self._row.append(' '.join(''.join(self._cell).split()))
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            self.rows.append((self.section, self._row))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        self.text.append(data)


def sldv_verdict(section):
    """The verdict of an SLDV section title, "Objectives Undecided Due to Stubbing"..."""
    title = section.lower()
    if 'valid under approximation' in title:
        return 'valid under approximation'
    if title.startswith('objectives valid'):
        return 'valid'
    if 'falsified' in title:
        return 'falsified' if 'counterexample' in title else 'falsified, not confirmed'
    if 'undecided' in title:
        return 'undecided'
    return title


def parse_sldv_report(text):
    """The model, date and proof objectives of an SLDV report (its HTML)"""
    parser = _ReportParser()
    parser.feed(text)
    words = ' '.join(' '.join(parser.text).split())
    model = re.search(r'Model: (\S+)', words)
    date = re.search(r'(\d{2}-[A-Z][a-z]{2}-\d{4} \d{2}:\d{2}:\d{2})', words)
    limit = re.search(r'Maximum Analysis Time: (\d+)s', words)
    objectives = []
    for section, row in parser.rows:
        if section and section.startswith('Objectives') and len(row) >= 5 and row[1] == 'Proof objective':
            time = float(row[4]) if re.fullmatch(r'\d+(\.\d*)?', row[4]) else None
            objectives.append({'property': row[2], 'verdict': sldv_verdict(section),
                               'status': section[len('Objectives '):], 'time': time})
    when = datetime.datetime.strptime(date.group(1), '%d-%b-%Y %H:%M:%S').isoformat() if date else None
    return {'model': model.group(1) if model else None, 'date': when,
            'limit': int(limit.group(1)) if limit else None, 'objectives': objectives}


def kind2_verdict(answer):
    return {'valid': 'valid', 'falsifiable': 'falsified'}.get(answer, 'undecided')


def parse_kind2_results(text):
    """Kind2's version, timeout and properties, from its XML output"""
    root = ElementTree.fromstring(text)
    version = None
    for log in root.iter('Log'):
        found = re.match(r'kind2 (v\S+)', (log.text or '').strip())
        if found:
            version = found.group(1)
            break
    properties = []
    for prop in root.iter('Property'):
        answer = prop.find('Answer')
        runtime = prop.find('Runtime')
        properties.append({
            'property': prop.get('name'), 'scope': prop.get('scope'),
            'answer': answer.text.strip() if answer is not None and answer.text else None,
            'verdict': kind2_verdict(answer.text.strip() if answer is not None and answer.text else None),
            'engine': answer.get('source') if answer is not None else None,
            'time': float(runtime.text) if runtime is not None and runtime.text else None})
    errors = [' '.join((log.text or '').split()) for log in root.iter('Log') if log.get('class') == 'error']
    timeout = root.get('timeout')
    return {'version': version, 'timeout': float(timeout) if timeout else None, 'properties': properties,
            'errors': errors}


def reported(arguments):
    """Writes the results the suite reports, SLDV's and Kind2's, as JSON"""
    suite = arguments.suite
    sldv = []
    for path in sorted(glob.glob(os.path.join(suite, 'FRET_CoCoSim', '*', 'SLDV', 'sldv_output', '*',
                                              '*_report*.html'))):
        with open(path, encoding='utf-8', errors='replace') as stream:
            report = parse_sldv_report(stream.read())
        report['source'] = os.path.relpath(path, suite)
        sldv.append(report)
    kind2 = []
    for path in sorted(glob.glob(os.path.join(suite, 'FRET_CoCoSim', '*', 'CoCoSim', 'cocosim_output', '**',
                                              '*.xml'), recursive=True)):
        if 'kind2' not in os.path.basename(path).lower():
            continue
        with open(path, encoding='utf-8', errors='replace') as stream:
            try:
                results = parse_kind2_results(stream.read())
            except ElementTree.ParseError as error:
                print(f'{path}: not read: {error}')
                continue
        relative = os.path.relpath(path, suite)
        parts = relative.split(os.sep)
        results.update(source=relative, design=parts[1],
                       model=parts[4] if len(parts) > 5 else parts[1])
        kind2.append(results)
    with open(arguments.out, 'w', encoding='utf-8') as out:
        json.dump({'suite_commit': LM_COMMIT, 'sldv': sldv, 'kind2': kind2}, out, indent=1)
    print(f'{arguments.out}: {len(sldv)} SLDV reports, {len(kind2)} Kind2 results')


# The report

def latest_sldv(reports):
    """(model, property) -> the objective of the latest SLDV report of the model"""
    latest = {}
    for report in sorted(reports, key=lambda r: r.get('date') or ''):
        for objective in report['objectives']:
            key = (report['model'], objective['property'])
            earlier = latest[key]['earlier'] + [latest[key]['verdict']] if key in latest else []
            latest[key] = dict(objective, date=report['date'], source=report['source'],
                               limit=report.get('limit'), earlier=earlier)
    return latest


def cell(record):
    if record is None:
        return '—'
    verdict = record['verdict']
    time = record.get('time')
    # SLDV gives -1 for an objective it did not analyze
    if time is not None and time >= 0 and verdict != 'unsupported':
        text = f'{verdict} ({time:.1f} s)' if time >= 1 else f'{verdict} ({time:.2f} s)'
    else:
        text = verdict
    earlier = [v for v in record.get('earlier', []) if v != verdict]
    return text + (f'; earlier run: {", ".join(earlier)}' if earlier else '')


def totals(records):
    counts = {verdict: 0 for verdict in VERDICTS}
    for record in records:
        counts[record['verdict']] = counts.get(record['verdict'], 0) + 1
    return counts


def plots(measured, sldv, modes, out_dir, timeout):
    """Solved over time for each mode and tool, and the times of the float
    mode against SLDV's, measured if it was, else reported; [] without
    matplotlib"""
    try:
        import matplotlib  # pylint: disable=import-outside-toplevel
        matplotlib.use('Agg')
        import matplotlib.pyplot as pyplot  # pylint: disable=import-outside-toplevel
    except ImportError:
        return []
    made = []
    figure, axes = pyplot.subplots(figsize=(6, 4))
    for mode in modes:
        times = sorted(r['time'] for r in measured.values()
                       if r['mode'] == mode and r['verdict'] in ('valid', 'falsified') and r.get('time') is not None)
        cumulative, total = [], 0.0
        for time in times:
            total += time
            cumulative.append(total)
        axes.step(cumulative, range(1, len(cumulative) + 1), where='post', label=MODE_TITLES[mode])
    axes.set_xlabel('cumulative time (s), measured')
    axes.set_ylabel('properties solved')
    axes.set_xscale('symlog')
    axes.legend(fontsize='small')
    figure.tight_layout()
    path = os.path.join(out_dir, 'solved-over-time.png')
    figure.savefig(path, dpi=120)
    pyplot.close(figure)
    made.append(('Properties solved over time (measured)', os.path.basename(path)))
    mode = next((m for m in modes if m in MODES), None)
    if 'sldv' in modes:
        others = {(k[0], k[1]): r for k, r in measured.items() if k[2] == 'sldv'}
        label, title = 'SLDV, measured (s)', 'intrepid against SLDV, both measured here'
    else:
        others = sldv
        label = 'SLDV, reported in 2019 (s), another machine'
        title = 'intrepid (measured) against SLDV (reported, another machine and year)'
    points = []
    for (model, prop, record_mode), record in measured.items():
        other = others.get((model, prop))
        if record_mode != mode or other is None or other.get('time') is None or record.get('time') is None:
            continue
        points.append((max(other['time'], 0.1), max(record['time'], 0.01)))
    if points:
        figure, axes = pyplot.subplots(figsize=(5, 5))
        axes.scatter([p[0] for p in points], [p[1] for p in points], s=12)
        axes.set_xscale('log')
        axes.set_yscale('log')
        axes.set_xlabel(label)
        axes.set_ylabel(f'{MODE_TITLES[mode]}, measured (s)')
        axes.axhline(timeout, linestyle=':', color='grey')
        low = min(min(p) for p in points)
        high = max(max(p) for p in points)
        axes.plot([low, high], [low, high], linestyle='--', color='grey', linewidth=0.8)
        figure.tight_layout()
        path = os.path.join(out_dir, 'intrepid-vs-sldv.png')
        figure.savefig(path, dpi=120)
        pyplot.close(figure)
        made.append((title, os.path.basename(path)))
    return made


def describe(mode, info, records):
    """What a mode or tool of the report is"""
    if mode in MODES:
        return {
            'float': 'singles and doubles as IEEE floats, exactly; the inputs within the design '
                     'ranges of their Inports, as SLDV takes them.',
            'real': 'singles and doubles as reals, as SLDV\'s rational arithmetic; the inputs '
                    'within their design ranges.',
            'float-finite': 'floats; the float inputs only finite.',
            'float-free': 'floats; the inputs anything, NaN and infinities included.'}[mode]
    versions = sorted({str(r.get('version') or r.get('kind2')) for r in records if r.get('version') or r.get('kind2')})
    limit = f'{info.get("timeout")} s times the number of properties of the model' if info else '?'
    if mode == 'sldv':
        return (f'SLDV {", ".join(versions) or "?"} ({info.get("matlab", "MATLAB") if info else "MATLAB"}), '
                f'property proving on the SLDV model, within {limit}. Rational arithmetic, as in 2019.')
    abstractions = sorted({name for r in records for name in r.get('abstractions', ())})
    unconfirmed = (' A counterexample through a function that CoCoSim\'s library only abstracts, '
                   f'bounding its result ({", ".join(abstractions)}; sin and cos in [-1, 1]), may be the '
                   'abstraction\'s: "falsified, not confirmed".'
                   if abstractions else '')
    primary = (info or {}).get('kind2') or ''
    others = {}
    for record in records:
        if record.get('kind2') and record['kind2'] not in primary:
            others.setdefault(record['kind2'], set()).add(record['model'])
    retried = ''.join(f' The models whose Lustre {primary} rejects were retried with {version}: '
                      f'{", ".join(sorted(models))}.' for version, models in sorted(others.items()))
    if mode == 'cocosim':
        return (f'CoCoSim (`{(info or {}).get("cocosim", "?")[:7]}`) translates the SLDV model to Lustre, and '
                f'{", ".join(versions) or "Kind2"} checks its Assertion and Proof Objective blocks with '
                f'CoCoSim\'s options (z3 as its solver), within {limit}. The times are Kind2\'s, without '
                f'the translation.{retried}{unconfirmed}')
    return (f'{", ".join(versions) or "Kind2"} on the Lustre that CoCoSim made in 2019 of the CoCoSim models '
            f'(their contracts, `--modular true` as CoCoSim), within {limit}. A requirement is given '
            f'the block of the SLDV model of the same name; the others are listed under their own.'
            f'{retried}{unconfirmed}')


def report(arguments):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Writes the report of runs and of the reported results, in Markdown"""
    with open(arguments.reported, encoding='utf-8') as stream:
        given = json.load(stream)
    infos = {}          # mode -> the machine record of its run
    measured = {}       # (model, property, mode) -> record
    unmatched = {}      # (model, mode) -> Kind2's properties with no SLDV block
    unsupported = {}    # (model, mode) -> error
    unsupported_designs = set()
    replays = {}        # (model, property, mode) -> the replay of intrepid's counterexample
    for path in arguments.runs:
        info = None
        for record in read_jsonl(path):
            if record.get('kind') == 'replay':
                replays[(record['model'], record['property'], record['mode'])] = record
            elif record.get('kind') == 'machine':
                info = info or record
            elif record.get('kind') == 'result':
                infos.setdefault(record['mode'], info)
                if record['property'] is None:
                    unsupported[(record['model'], record['mode'])] = (record['verdict'], record.get('error', ''))
                    unsupported_designs.add(record['design'])
                elif record.get('unmatched'):
                    unmatched.setdefault((record['model'], record['mode']), []).append(record)
                else:
                    measured[(record['model'], record['property'], record['mode'])] = record
    present = {k[2] for k in measured} | {k[1] for k in unsupported} | {k[1] for k in unmatched}
    modes = [m for m in list(MODES) + list(TOOL_TITLES) if m in present]
    sldv = latest_sldv(given['sldv'])
    analysed = {k[0] for k in measured} | {k[0] for k in unsupported}
    if analysed:
        # the same models for every tool: those of the runs (all, or --filter's)
        sldv = {key: objective for key, objective in sldv.items() if key[0] in analysed}
    model_names = sorted(analysed | {k[0] for k in sldv})
    out_dir = os.path.dirname(os.path.abspath(arguments.out))
    os.makedirs(out_dir, exist_ok=True)
    lines = ['# intrepid, SLDV and CoCoSim on the Lockheed Martin challenges', '']
    lines += ['The SLDV models of the challenges (`FRET_CoCoSim/*/SLDV`, lm_challenges at '
              f'`{given["suite_commit"][:7]}`), one property at a time.', '']
    lines += ['## How', '']
    machines = []
    for mode in modes:
        info = infos.get(mode)
        if info and info not in machines:
            machines.append(info)
    for info in machines:
        tools = ', '.join(MODE_TITLES[m] for m in modes if infos.get(m) is info)
        lines.append(f'- **Measured** on {info.get("date")}: {info.get("cpu")}, {info.get("cores")} threads, '
                     f'{info.get("memory_gb", "?")} GB, {info.get("platform")}; Python {info.get("python")}, '
                     f'intrepyd {info.get("intrepyd")}, Simulink translator {info.get("translator")}; '
                     f'{info.get("timeout")} s per property'
                     + (f', {info["memory_per_engine_gb"]:g} GB per engine' if info.get('memory_per_engine_gb') else '')
                     + f' ({tools})'
                     + ''.join(f'; {name} {info[name]}' for name in ('matlab', 'cocosim', 'kind2', 'z3')
                               if info.get(name)) + '.')
    for mode in modes:
        records = [r for k, r in measured.items() if k[2] == mode]
        lines.append(f'- **{MODE_TITLES[mode]}** (`{mode}`): ' + describe(mode, infos.get(mode), records))
    dates = sorted({o['date'][:10] for o in sldv.values() if o.get('date')})
    lines.append('- **SLDV: reported**, not measured: the reports in the suite '
                 f'(`sldv_output`), made from {dates[0] if dates else "?"} to {dates[-1] if dates else "?"} '
                 'on another machine, with rational arithmetic (floats approximated), each within its '
                 f'own time limit ({", ".join(sorted({str(o["limit"]) + " s" for o in sldv.values() if o.get("limit")}, key=lambda t: float(t[:-2])))} '
                 'per analysis). For each model, its latest report; an earlier run that said otherwise '
                 'is noted. Their times are not comparable with the measured ones.')
    versions = sorted({k['version'] for k in given['kind2'] if k.get('version')})
    lines.append('- **CoCoSim: reported**: Kind2 '
                 f'({", ".join(versions) or "version not given"}) on CoCoSim\'s Lustre of the '
                 '`CoCoSim` models, whose properties are named after the requirements, not after '
                 'the SLDV models\' blocks: they are given per design, below.')
    lines.append('')
    lines += ['## Totals', '', '| Tool | ' + ' | '.join(VERDICTS) + ' |',
              '| ---- | ' + ' | '.join('-' * len(v) for v in VERDICTS) + ' |']
    sldv_counts = totals(sldv.values())
    lines.append('| SLDV (reported) | ' + ' | '.join(str(sldv_counts[v]) for v in VERDICTS) + ' |')
    for mode in modes:
        records = [r for k, r in measured.items() if k[2] == mode]
        counts = totals(records)
        # the properties of the models not analysed, as SLDV lists them
        for key in sldv:
            if (key[0], mode) in unsupported:
                counts[unsupported[(key[0], mode)][0]] += 1
        lines.append(f'| {MODE_TITLES[mode]} | ' + ' | '.join(str(counts[v]) for v in VERDICTS) + ' |')
    lines += ['', 'A model a tool does not translate counts its SLDV properties as unsupported (as errors, '
              'when the tool failed on it). The properties of Kind2 that name no block of the SLDV model '
              'are not counted: they are listed with their model.', '']
    for title, path in plots(measured, sldv, modes, out_dir, machines[0].get('timeout', 0) if machines else 0):
        lines += [f'![{title}]({path})', '']
    lines += ['## Per property', '']
    disagreements = []
    for model in model_names:
        properties = sorted({k[1] for k in measured if k[0] == model} | {k[1] for k in sldv if k[0] == model})
        lines += [f'### {model}', '']
        for mode in modes:
            if (model, mode) in unsupported:
                verdict, error = unsupported[(model, mode)]
                error = error.strip().splitlines()
                what = 'not translated' if verdict == 'unsupported' else 'failed'
                if len(error) > 1 and error[0].endswith(':'):
                    # a count, then one error per line
                    error = [error[0] + ' ' + '; '.join(line.strip() for line in error[1:])]
                lines += [f'{MODE_TITLES[mode]}: {what}: {error[0] if error else ""}', '']
        lines += ['| Property | SLDV (reported) | ' + ' | '.join(MODE_TITLES[m] for m in modes) + ' |',
                  '| -------- | --------------- | ' + ' | '.join('-' * len(MODE_TITLES[m]) for m in modes) + ' |']
        for prop in properties:
            objective = sldv.get((model, prop))
            row = [cell(objective)]
            verdicts = {'SLDV (reported)': objective['verdict']} if objective else {}
            for mode in modes:
                record = measured.get((model, prop, mode))
                if record is None and (model, mode) in unsupported:
                    record = {'verdict': unsupported[(model, mode)][0]}
                row.append(cell(record))
                if record:
                    verdicts[MODE_TITLES[mode]] = record['verdict']
            holds = [t for t, v in verdicts.items() if v in ('valid', 'valid under approximation')]
            fails = [t for t, v in verdicts.items() if v == 'falsified']
            if holds and fails:
                disagreements.append((model, prop, holds, fails))
            lines.append(f'| {prop} | ' + ' | '.join(row) + ' |')
        lines.append('')
        for mode in modes:
            others = unmatched.get((model, mode))
            if others:
                lines += [f'{MODE_TITLES[mode]}, properties naming no block of the SLDV model: '
                          + ', '.join(f'`{r["property"]}` {cell(r)}' for r in others), '']
    lines += ['## Disagreements', '']
    if disagreements:
        lines += ['A property valid by some and falsified by others. With floats, the IEEE semantics '
                  '(rounding, overflow, NaN) can falsify what holds of rationals; SLDV\'s "valid under '
                  'approximation" may not hold exactly: each is to be explained. The last column says whether '
                  'intrepid\'s counterexample fails in Simulink too (see below): if it does, the property '
                  'fails in the model as Simulink computes it, not in the translation alone.',
                  '', '| Model | Property | Valid by | Falsified by | In Simulink |',
                  '| ----- | -------- | -------- | ------------ | ----------- |']
        titled = {title: mode for mode, title in MODE_TITLES.items()}
        for m, p, h, f in disagreements:
            seen = []
            for title in f:
                record = replays.get((m, p, titled.get(title)))
                if record is not None:
                    seen.append(f'{title}: ' + (f'fails at step {record["simulink_step"]}'
                                                if record['verdict'] == 'confirmed' else record['verdict']))
            lines.append(f'| {m} | {p} | {", ".join(h)} | {", ".join(f)} | {"; ".join(seen) or "not replayed"} |')
    else:
        lines.append('None.')
    if replays:
        lines += ['', "## intrepid's counterexamples in Simulink", '',
                  "Each counterexample of intrepid, its inputs at each step given to the SLDV model simulated in "
                  "Simulink (MATLAB R2018a, its Assertion blocks not stopping it): the first step at which the "
                  "property block's input is false there, if any. With reals, a counterexample's values are "
                  "rounded to doubles: it may legitimately not fail in Simulink.", '',
                  "| Model | Property | Mode | intrepid: fails at step | Simulink |",
                  '| ----- | -------- | ---- | ----------------------- | -------- |']
        for (model, prop, mode), record in sorted(replays.items()):
            if record['verdict'] == 'confirmed':
                outcome = f'fails at step {record["simulink_step"]}'
            elif record['verdict'] == 'not confirmed':
                outcome = '**holds at every step**'
            else:
                outcome = f'{record["verdict"]}: {record.get("error", "")}'
            last = record['steps'] - 1 if record.get('steps') else ''
            lines.append(f'| {model} | {prop} | {MODE_TITLES.get(mode, mode)} | {last} | {outcome} |')
    lines += ['', '## CoCoSim and Kind2 (reported), per design', '',
              'All the properties of each result, those CoCoSim adds included (the contracts of its '
              'library blocks, whether the modes of a contract are exhaustive), with the timeout of '
              'that analysis.', '']
    designs = {r['design'] for r in measured.values()} | unsupported_designs
    for results in sorted(given['kind2'], key=lambda r: r['source']):
        if designs and results['design'] not in designs:
            continue
        counts = totals(results['properties'])
        summary = ', '.join(f'{counts[v]} {v}' for v in VERDICTS if counts[v])
        lines += [f'- **{results["model"]}** (`{results["source"]}`, timeout {results.get("timeout")} s): '
                  f'{len(results["properties"])} properties, {summary or "none"}']
    lines.append('')
    with open(arguments.out, 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines))
    print(f'{arguments.out}: {len(measured)} measured results, {len(sldv)} SLDV objectives')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0].strip())
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('fetch', help='clone the challenges at the pinned commit')
    command.add_argument('--into', default=SUITE)
    command = commands.add_parser('run', help='run intrepid on the SLDV models')
    command.add_argument('out')
    command.add_argument('--memory', type=float, default=8.0,
                         help='gigabytes each engine may use (8: four engines in 32)')
    command.add_argument('--modes', default=','.join(DEFAULT_MODES), help=f'among {", ".join(MODES)}')
    command.add_argument('--timeout', type=float, default=120.0, help='seconds per property')
    command.add_argument('--filter', help='only the models whose path matches this regular expression')
    command.add_argument('--suite', default=SUITE)
    command = commands.add_parser('fetch-cocosim', help='clone CoCoSim at the pinned commit, and set it up')
    command.add_argument('--into', default=matlab.COCOSIM)
    for name, helped in (('sldv', 'run SLDV on the SLDV models (MATLAB)'),
                         ('cocosim', 'run CoCoSim and Kind2 on the SLDV models (MATLAB)'),
                         ('kind2', "run Kind2 on CoCoSim's Lustre of 2019 (no MATLAB)")):
        command = commands.add_parser(name, help=helped)
        command.add_argument('out')
        command.add_argument('--timeout', type=float, default=120.0,
                             help='seconds per property; an analysis has this times its properties')
        command.add_argument('--filter', help='only the models whose path matches this regular expression')
        command.add_argument('--suite', default=SUITE)
        if name in ('sldv', 'cocosim'):
            command.add_argument('--matlab', required=True, help='the MATLAB executable (or its launcher)')
            command.add_argument('--cocosim', default=matlab.COCOSIM, help='CoCoSim, set up by fetch-cocosim')
        if name in ('cocosim', 'kind2'):
            command.add_argument('--kind2', required=True, help='the Kind2 executable')
            command.add_argument('--z3', required=True, help='the z3 executable, for Kind2')
        if name in ('cocosim', 'kind2'):
            command.add_argument('--fallback-kind2',
                                 help='a Kind2 to retry the models with whose Lustre --kind2 rejects')
    command = commands.add_parser('check', help='(internal) check one model in one mode')
    command.add_argument('model')
    command.add_argument('mode', choices=MODES)
    command.add_argument('--timeout', type=float, default=120.0)
    command.add_argument('--suite', default=SUITE)
    command.add_argument('--property', action='append', help='only this property (again for more)')
    command.add_argument('--memory', type=float, help='gigabytes each engine may use')
    command = commands.add_parser('replay', help="replay intrepid's counterexamples in Simulink (MATLAB)")
    command.add_argument('runs', nargs='+', help='the results of intrepid')
    command.add_argument('--out', required=True, help='where the replays go, JSON lines')
    command.add_argument('--matlab', required=True, help='the MATLAB executable (or its launcher)')
    command.add_argument('--cocosim', default=matlab.COCOSIM, help='CoCoSim, set up by fetch-cocosim')
    command.add_argument('--timeout', type=float, default=120.0,
                         help='seconds per property, to find again a counterexample the results lack')
    command.add_argument('--filter', help='only the models whose path matches this regular expression')
    command.add_argument('--suite', default=SUITE)
    command = commands.add_parser('reported', help='read the results the suite reports')
    command.add_argument('out')
    command.add_argument('--suite', default=SUITE)
    command = commands.add_parser('report', help='write the report')
    command.add_argument('runs', nargs='+')
    command.add_argument('--reported', required=True)
    command.add_argument('--out', default=os.path.join(ROOT, 'results', 'report.md'))
    arguments = parser.parse_args()
    if arguments.command == 'fetch':
        fetch(arguments.into)
    elif arguments.command == 'fetch-cocosim':
        matlab.fetch_cocosim(arguments.into)
    elif arguments.command == 'run':
        run(arguments)
    elif arguments.command in ('sldv', 'cocosim', 'kind2'):
        for name in ('cocosim', 'kind2', 'fallback_kind2', 'z3'):
            if getattr(arguments, name, None):
                setattr(arguments, name, os.path.abspath(getattr(arguments, name)))
        if arguments.command == 'cocosim' and not os.path.exists(os.path.join(arguments.cocosim, matlab.SETUP_MARK)):
            raise SystemExit(f'no CoCoSim set up in {arguments.cocosim}: run "python simulink/harness.py fetch-cocosim"')
        {'sldv': sldv, 'cocosim': cocosim, 'kind2': kind2_2019}[arguments.command](arguments)
    elif arguments.command == 'check':
        check_model(arguments.suite, arguments.model, arguments.mode, arguments.timeout,
                    lambda record: print(json.dumps(record), flush=True), arguments.property, arguments.memory)
    elif arguments.command == 'replay':
        for name in ('cocosim', 'out'):
            setattr(arguments, name, os.path.abspath(getattr(arguments, name)))
        replay(arguments)
    elif arguments.command == 'reported':
        reported(arguments)
    else:
        report(arguments)


if __name__ == '__main__':
    main()
