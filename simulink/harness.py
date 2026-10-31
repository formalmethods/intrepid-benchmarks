"""
The Simulink benchmark: intrepid against Simulink Design Verifier (SLDV) and
CoCoSim on the designs of the Lockheed Martin cyber-physical challenges, as
NASA formalized them (https://github.com/hbourbouh/lm_challenges).

    python simulink/harness.py fetch [--into third_party/lm_challenges]
    python simulink/harness.py run OUT.jsonl [--modes float,real] [--timeout 120]
        [--filter REGEX] [--suite PATH]
    python simulink/harness.py reported OUT.json [--suite PATH]
    python simulink/harness.py report OUT.jsonl... --reported REPORTED.json
        [--out results/report.md]

fetch clones the challenges at the commit the benchmark is made for. The
models are not copied anywhere: they stay in that checkout, on the machine
that runs the benchmark.

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
import glob
import html.parser
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree

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


def check_model(suite, model, mode, timeout, emit):
    """Translates a model in a mode and checks each target; emit(record) for each"""
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
    prefix = model_name(model) + '/'
    for name, target in circuit.targets.items():
        portfolio = ctx.mk_portfolio()
        portfolio.add_target(target)
        record = dict(base, property=name[len(prefix):] if name.startswith(prefix) else name)
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
            if abstractions:
                # through a function abstracted as an input, the
                # counterexample may be the abstraction's: replayed with the
                # functions themselves
                reached, _ = simulink.confirm_counterexample(ctx, circuit, portfolio.get_last_trace(), target)
                if not reached:
                    record['verdict'] = 'falsified, not confirmed'
        else:
            record['verdict'] = 'undecided'
        emit(record)


def read_jsonl(path):
    records = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as stream:
            for line in stream:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def run(arguments):
    """Runs every model in every mode not in OUT yet, each in a process of its own"""
    suite = os.path.abspath(arguments.suite)
    if not os.path.isdir(os.path.join(suite, 'FRET_CoCoSim')):
        raise SystemExit(f'no suite in {suite}: run "python simulink/harness.py fetch" first')
    modes = arguments.modes.split(',')
    for mode in modes:
        if mode not in MODES:
            raise SystemExit(f'unknown mode {mode}: {", ".join(MODES)}')
    out_path = os.path.abspath(arguments.out)
    done = {(r['model'], r['mode']) for r in read_jsonl(out_path) if r.get('kind') == 'done'}
    with open(out_path, 'a', encoding='utf-8') as out:
        if not read_jsonl(out_path):
            info = machine()
            info.update(timeout=arguments.timeout, suite_commit=LM_COMMIT)
            out.write(json.dumps(info) + '\n')
            out.flush()
        for model in models(suite):
            if arguments.filter and not re.search(arguments.filter, model):
                continue
            for mode in modes:
                if (model_name(model), mode) in done:
                    continue
                print(f'{model_name(model)} ({mode})', flush=True)
                command = [sys.executable, os.path.abspath(__file__), 'check', model, mode,
                           '--timeout', str(arguments.timeout), '--suite', suite]
                # in a folder of its own: what the check leaves behind (intrepid
                # writes trace.cpp where it meets an error) goes with it
                scratch = tempfile.mkdtemp(prefix='simulink-check-')
                process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True,  # pylint: disable=consider-using-with
                                           cwd=scratch)
                for line in process.stdout:
                    if line.startswith('{'):
                        record = json.loads(line)
                        out.write(line)
                        out.flush()
                        print(f'  {record.get("property")}: {record["verdict"]}'
                              + (f' in {record["time"]} s' if 'time' in record else ''), flush=True)
                status = process.wait()
                shutil.rmtree(scratch, ignore_errors=True)
                if status == 0:
                    out.write(json.dumps({'kind': 'done', 'model': model_name(model), 'mode': mode}) + '\n')
                    out.flush()
                else:
                    print(f'  the check of {model_name(model)} ({mode}) failed: run it again to retry it',
                          flush=True)


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
    timeout = root.get('timeout')
    return {'version': version, 'timeout': float(timeout) if timeout else None, 'properties': properties}


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
    if time is not None and verdict != 'unsupported':
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
    """Solved over time for each mode, and the times of the float mode against
    SLDV's reported ones; [] without matplotlib"""
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
                       if r['mode'] == mode and r['verdict'] in ('valid', 'falsified') and 'time' in r)
        cumulative, total = [], 0.0
        for time in times:
            total += time
            cumulative.append(total)
        axes.step(cumulative, range(1, len(cumulative) + 1), where='post', label=MODE_TITLES[mode])
    axes.set_xlabel('cumulative time (s), measured')
    axes.set_ylabel('properties solved')
    axes.set_xscale('symlog')
    axes.legend()
    figure.tight_layout()
    path = os.path.join(out_dir, 'solved-over-time.png')
    figure.savefig(path, dpi=120)
    pyplot.close(figure)
    made.append(('Properties solved over time (measured)', os.path.basename(path)))
    mode = modes[0]
    points = []
    for (model, prop, record_mode), record in measured.items():
        objective = sldv.get((model, prop))
        if record_mode != mode or objective is None or objective.get('time') is None or 'time' not in record:
            continue
        points.append((max(objective['time'], 0.1), max(record['time'], 0.01)))
    if points:
        figure, axes = pyplot.subplots(figsize=(5, 5))
        axes.scatter([p[0] for p in points], [p[1] for p in points], s=12)
        axes.set_xscale('log')
        axes.set_yscale('log')
        axes.set_xlabel('SLDV, reported in 2019 (s), another machine')
        axes.set_ylabel(f'{MODE_TITLES[mode]}, measured (s)')
        axes.axhline(timeout, linestyle=':', color='grey')
        low = min(min(p) for p in points)
        high = max(max(p) for p in points)
        axes.plot([low, high], [low, high], linestyle='--', color='grey', linewidth=0.8)
        figure.tight_layout()
        path = os.path.join(out_dir, 'intrepid-vs-sldv.png')
        figure.savefig(path, dpi=120)
        pyplot.close(figure)
        made.append(('intrepid (measured) against SLDV (reported, another machine and year)',
                     os.path.basename(path)))
    return made


def report(arguments):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Writes the report of runs and of the reported results, in Markdown"""
    with open(arguments.reported, encoding='utf-8') as stream:
        given = json.load(stream)
    info = None
    measured = {}       # (model, property, mode) -> record
    unsupported = {}    # (model, mode) -> error
    unsupported_designs = set()
    for path in arguments.runs:
        for record in read_jsonl(path):
            if record.get('kind') == 'machine' and info is None:
                info = record
            elif record.get('kind') == 'result':
                if record['property'] is None:
                    unsupported[(record['model'], record['mode'])] = record.get('error', '')
                    unsupported_designs.add(record['design'])
                else:
                    measured[(record['model'], record['property'], record['mode'])] = record
    modes = [m for m in MODES if any(r['mode'] == m for r in measured.values())
             or any(k[1] == m for k in unsupported)]
    sldv = latest_sldv(given['sldv'])
    run_models = {k[0] for k in measured} | {k[0] for k in unsupported}
    if run_models:
        # the same models for every tool: those of the runs (all, or --filter's)
        sldv = {key: objective for key, objective in sldv.items() if key[0] in run_models}
    model_names = sorted(run_models | {k[0] for k in sldv})
    out_dir = os.path.dirname(os.path.abspath(arguments.out))
    os.makedirs(out_dir, exist_ok=True)
    lines = ['# intrepid, SLDV and CoCoSim on the Lockheed Martin challenges', '']
    lines += ['The SLDV models of the challenges (`FRET_CoCoSim/*/SLDV`, lm_challenges at '
              f'`{given["suite_commit"][:7]}`), one property at a time.', '']
    lines += ['## How', '']
    if info:
        lines += [f'- **Measured** on {info.get("date")}: {info.get("cpu")}, {info.get("cores")} threads, '
                  f'{info.get("memory_gb", "?")} GB, {info.get("platform")}; Python {info.get("python")}, '
                  f'intrepyd {info.get("intrepyd")}, Simulink translator {info.get("translator")}; '
                  f'{info.get("timeout")} s per property, intrepyd\'s portfolio.']
    for mode in modes:
        lines.append(f'- **{MODE_TITLES[mode]}** (`{mode}`): ' + {
            'float': 'singles and doubles as IEEE floats, exactly; the inputs within the design '
                     'ranges of their Inports, as SLDV takes them.',
            'real': 'singles and doubles as reals, as SLDV\'s rational arithmetic; the inputs '
                    'within their design ranges.',
            'float-finite': 'floats; the float inputs only finite.',
            'float-free': 'floats; the inputs anything, NaN and infinities included.'}[mode])
    dates = sorted({o['date'][:10] for o in sldv.values() if o.get('date')})
    lines.append('- **SLDV: reported**, not measured: the reports in the suite '
                 f'(`sldv_output`), made from {dates[0] if dates else "?"} to {dates[-1] if dates else "?"} '
                 'on another machine, with rational arithmetic (floats approximated), each within its '
                 f'own time limit ({", ".join(sorted({str(o["limit"]) + " s" for o in sldv.values() if o.get("limit")}, key=lambda t: float(t[:-2])))} '
                 'per analysis). For each model, its latest report; an earlier run that said otherwise '
                 'is noted. Their times are not comparable with ours.')
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
        records = [r for r in measured.values() if r['mode'] == mode]
        counts = totals(records)
        # the properties of the models not translated, as SLDV lists them
        counts['unsupported'] += sum(1 for key in sldv if (key[0], mode) in unsupported)
        lines.append(f'| {MODE_TITLES[mode]} | ' + ' | '.join(str(counts[v]) for v in VERDICTS) + ' |')
    lines += ['', 'A model intrepid does not translate counts its SLDV properties as unsupported.', '']
    for title, path in plots(measured, sldv, modes, out_dir, info.get('timeout', 0) if info else 0):
        lines += [f'![{title}]({path})', '']
    lines += ['## Per property', '']
    disagreements = []
    for model in model_names:
        properties = sorted({k[1] for k in measured if k[0] == model} | {k[1] for k in sldv if k[0] == model})
        lines += [f'### {model}', '']
        for mode in modes:
            if (model, mode) in unsupported:
                error = unsupported[(model, mode)].strip().splitlines()
                lines += [f'{MODE_TITLES[mode]}: not translated: {error[0] if error else ""}', '']
        lines += ['| Property | SLDV (reported) | ' + ' | '.join(MODE_TITLES[m] for m in modes) + ' |',
                  '| -------- | --------------- | ' + ' | '.join('-' * len(MODE_TITLES[m]) for m in modes) + ' |']
        for prop in properties:
            objective = sldv.get((model, prop))
            row = [cell(objective)]
            for mode in modes:
                record = measured.get((model, prop, mode))
                if record is None and (model, mode) in unsupported:
                    record = {'verdict': 'unsupported'}
                row.append(cell(record))
                if objective and record and {objective['verdict'], record['verdict']} in (
                        {'valid', 'falsified'}, {'valid under approximation', 'falsified'}):
                    disagreements.append((model, prop, mode, objective['verdict'], record['verdict']))
            lines.append(f'| {prop} | ' + ' | '.join(row) + ' |')
        lines.append('')
    lines += ['## Disagreements', '']
    if disagreements:
        lines += ['A property valid by one and falsified by the other. With floats, the IEEE semantics '
                  '(rounding, overflow, NaN) can falsify what holds of rationals: each is to be explained.',
                  '', '| Model | Property | Mode | SLDV | intrepid |', '| ----- | -------- | ---- | ---- | -------- |']
        lines += [f'| {m} | {p} | {mode} | {a} | {b} |' for m, p, mode, a, b in disagreements]
    else:
        lines.append('None.')
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
    command.add_argument('--modes', default=','.join(DEFAULT_MODES), help=f'among {", ".join(MODES)}')
    command.add_argument('--timeout', type=float, default=120.0, help='seconds per property')
    command.add_argument('--filter', help='only the models whose path matches this regular expression')
    command.add_argument('--suite', default=SUITE)
    command = commands.add_parser('check', help='(internal) check one model in one mode')
    command.add_argument('model')
    command.add_argument('mode', choices=MODES)
    command.add_argument('--timeout', type=float, default=120.0)
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
    elif arguments.command == 'run':
        run(arguments)
    elif arguments.command == 'check':
        check_model(arguments.suite, arguments.model, arguments.mode, arguments.timeout,
                    lambda record: print(json.dumps(record), flush=True))
    elif arguments.command == 'reported':
        reported(arguments)
    else:
        report(arguments)


if __name__ == '__main__':
    main()
