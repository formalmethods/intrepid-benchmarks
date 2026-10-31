"""
Tests of simulink/harness.py that need neither intrepyd nor the suite: the
readers of SLDV's reports and of Kind2's results, on small documents of the
same shape written here, and the report, on runs made up here.

    python -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'simulink'))
import harness  # pylint: disable=wrong-import-position


def _row(number, item, time, counterexample='n/a'):
    return (f'<tr><td align="left">{number}</td><td align="left">Proof objective</td>'
            f'<td align="left"><a class="ulink" href="#">{item}</a></td><td align="left">Objective: T</td>'
            f'<td align="left">{time}</td><td align="left">{counterexample}</td></tr>')


def _section(title, rows):
    return (f'<div class="sect1" title="{title}"><h2 class="title">{title}</h2>'
            '<table><thead><tr><th>#</th><th>Type</th><th>Model Item</th><th>Description</th>'
            '<th>Analysis Time (sec)</th><th>Counterexample</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></div>')


SLDV_REPORT = (
    '<html><body><h1>Simulink Design Verifier Report</h1><p>m_SLDV</p><p>someone</p>'
    '<p>06-Nov-2019 16:02:37</p>'
    '<div class="sect1" title="Summary"><table><tr><td>Model:</td><td>m_SLDV</td></tr>'
    '<tr><td>Maximum Analysis Time:</td><td>300s</td></tr></table></div>'
    + _section('Objectives Valid', [_row(1, 'Spec/Assertion1', 8)])
    + _section('Objectives Falsified with Counterexamples', [_row(2, 'Spec/Assertion2', 17, '1')])
    + _section('Objectives Valid under Approximation', [_row(3, 'Spec/Assertion3', 12)])
    + _section('Objectives Undecided Due to Nonlinearities', [_row(4, 'Spec/Assertion4', 'n/a')])
    + '</body></html>')

KIND2_RESULTS = """<?xml version="1.0"?>
<Results enabled="bmc,ind,ic3" timeout="300.000000">
<Log class="info" source="parse">kind2 v1.1.0</Log>
<AnalysisStart top="top"/>
<Property name="Spec.REQ_001[1]" line="1" column="1" scope="top">
<Runtime unit="sec" timeout="false">0.5</Runtime><Answer source="ind2">valid</Answer></Property>
<Property name="Spec.REQ_002[2]" line="2" column="1" scope="top">
<Runtime unit="sec" timeout="false">0.8</Runtime><Answer source="bmc">falsifiable</Answer></Property>
<Property name="Spec.REQ_003[3]" line="3" column="1" scope="top">
<Runtime unit="sec" timeout="true">300.0</Runtime><Answer source="kind2">unknown</Answer></Property>
<AnalysisStop/>
</Results>
"""


class TestReaders(unittest.TestCase):

    def test_an_sldv_report(self):
        report = harness.parse_sldv_report(SLDV_REPORT)
        self.assertEqual('m_SLDV', report['model'])
        self.assertEqual('2019-11-06T16:02:37', report['date'])
        self.assertEqual(300, report['limit'])
        self.assertEqual([('Spec/Assertion1', 'valid', 8.0), ('Spec/Assertion2', 'falsified', 17.0),
                          ('Spec/Assertion3', 'valid under approximation', 12.0),
                          ('Spec/Assertion4', 'undecided', None)],
                         [(o['property'], o['verdict'], o['time']) for o in report['objectives']])
        self.assertEqual('Undecided Due to Nonlinearities', report['objectives'][3]['status'])

    def test_kind2_results(self):
        results = harness.parse_kind2_results(KIND2_RESULTS)
        self.assertEqual('v1.1.0', results['version'])
        self.assertEqual(300.0, results['timeout'])
        self.assertEqual([('Spec.REQ_001[1]', 'valid', 'ind2', 0.5), ('Spec.REQ_002[2]', 'falsified', 'bmc', 0.8),
                          ('Spec.REQ_003[3]', 'undecided', 'kind2', 300.0)],
                         [(p['property'], p['verdict'], p['engine'], p['time']) for p in results['properties']])

    def test_the_latest_sldv_run_with_the_earlier_verdicts(self):
        first = {'model': 'm', 'date': '2019-11-01T16:00:00', 'source': 'a', 'limit': 300,
                 'objectives': [{'property': 'p', 'verdict': 'falsified', 'time': 19.0}]}
        second = dict(first, date='2019-11-01T17:00:00', source='b',
                      objectives=[{'property': 'p', 'verdict': 'undecided', 'time': 8.0}])
        latest = harness.latest_sldv([second, first])
        self.assertEqual('undecided', latest[('m', 'p')]['verdict'])
        self.assertEqual('undecided (8.0 s); earlier run: falsified', harness.cell(latest[('m', 'p')]))


class TestSuite(unittest.TestCase):

    def test_models_and_their_data(self):
        with tempfile.TemporaryDirectory() as suite:
            for path in ('FRET_CoCoSim/7_ap/SLDV/b_SLDV.slx', 'FRET_CoCoSim/7_ap/SLDV/a_SLDV.mdl',
                         'FRET_CoCoSim/7_ap/SLDV/sldv_output/b_SLDV/b_harness.slx',
                         'FRET_CoCoSim/7_ap/ap_data.mat', 'FRET_CoCoSim/7_ap/CoCoSim/b.slx'):
                os.makedirs(os.path.dirname(os.path.join(suite, path)), exist_ok=True)
                open(os.path.join(suite, path), 'w', encoding='utf-8').close()
            found = harness.models(suite)
            self.assertEqual([os.path.join('FRET_CoCoSim', '7_ap', 'SLDV', name) for name in ('a_SLDV.mdl', 'b_SLDV.slx')],
                             found)
            self.assertEqual('7_ap', harness.design_of(found[0]))
            self.assertEqual('a_SLDV', harness.model_name(found[0]))
            self.assertEqual([os.path.join(suite, 'FRET_CoCoSim', '7_ap', 'ap_data.mat')],
                             harness.data_files(suite, found[0]))


# A model's diagram as an SLX holds it: a subsystem with an Assertion fed by
# the subsystem of its requirement, and a Proof Objective named after its own
BLOCK_DIAGRAM = """<?xml version="1.0" encoding="utf-8"?>
<ModelInformation><Model Name="m_SLDV"><System>
<Block BlockType="SubSystem" Name="Spec" SID="1"><System>
<Block BlockType="SubSystem" Name="AP-003A" SID="2"/>
<Block BlockType="Assertion" Name="Assertion1" SID="3"/>
<Block BlockType="Reference" Name="EUL_002_1" SID="4">
<P Name="SourceBlock">sldvlib/Objectives and Constraints/Proof Objective</P></Block>
<Block BlockType="Inport" Name="x" SID="5"/>
<Line><P Name="Src">2#out:1</P><P Name="Dst">3#in:1</P></Line>
<Line><P Name="Src">5#out:1</P><Branch><P Name="Dst">4#in:1</P></Branch><Branch><P Name="Dst">2#in:1</P></Branch></Line>
</System></Block>
</System></Model></ModelInformation>
"""

KIND2_V3_RESULTS = """<?xml version="1.0"?>
<Results enabled="bmc,ind,ic3" timeout="240.000000">
<Log class="info" source="parse">kind2 v3.0.0</Log>
<Property name="Spec_1_000[L9C2].AP_minus_003A_3098_002" scope="top">
<Runtime unit="sec" timeout="false">0.19</Runtime><Answer source="bmc">falsifiable</Answer></Property>
<Property name="Spec_1_000[L9C2].hdg_hold_mode_3594_001[L865C3]" scope="top">
<Runtime unit="sec" timeout="false">0.05</Runtime><Answer source="bmc">reachable</Answer></Property>
<Property name="Spec_1_000._one_mode_active" scope="top">
<Runtime unit="sec" timeout="false">0.05</Runtime><Answer source="ic3qe">valid</Answer></Property>
</Results>
"""


class TestTools(unittest.TestCase):

    def test_the_properties_of_an_slx_model(self):
        with tempfile.TemporaryDirectory() as suite:
            model = os.path.join('FRET_CoCoSim', '7_ap', 'SLDV', 'm_SLDV.slx')
            os.makedirs(os.path.join(suite, os.path.dirname(model)))
            with zipfile.ZipFile(os.path.join(suite, model), 'w') as archive:
                archive.writestr('simulink/blockdiagram.xml', BLOCK_DIAGRAM)
            self.assertEqual({'Spec/Assertion1': ['Assertion1', 'AP-003A'], 'Spec/EUL_002_1': ['EUL_002_1', 'x']},
                             harness.property_blocks(suite, model))
            self.assertEqual({}, harness.property_blocks(suite, model[:-4] + '.mdl'))

    def test_requirements_by_name(self):
        for block, lustre in (('AP-003A', 'AP_minus_003A_3098_002'), ('EUL_002_1', 'EUL002_1_245_150'),
                              ('roll_hold_mode', 'roll_hold_mode_ensure_3648_001'),
                              ('SWIM001A', 'SWIM_minus_001A_340_035'), ('TSM-003b', 'TSM_minus_003b_1435_004')):
            self.assertEqual(harness.requirement_key(block), harness.requirement_key(lustre, lustre=True), block)
        self.assertNotEqual(harness.requirement_key('REG001'), harness.requirement_key('REG0010_8613_000', lustre=True))

    def test_sldv_statuses(self):
        self.assertEqual(['valid', 'valid under approximation', 'falsified', 'falsified, not confirmed',
                          'undecided'],
                         [harness.sldv_status_verdict(s) for s in (
                             'Valid', 'Valid under approximation', 'Falsified',
                             'Falsified - No Counterexample', 'Undecided due to nonlinearities')])

    def test_kind2_results_on_sldv_blocks(self):
        results = harness.parse_kind2_results(KIND2_V3_RESULTS)
        results['properties'] = [p for p in results['properties'] if p['answer'] not in ('reachable', 'unreachable')]
        keys = {harness.requirement_key('AP-003A'): 'Spec/Assertion1'}
        records = harness.kind2_records({'mode': 'kind2-2019'}, results,
                                        lambda prop: keys.get(harness.requirement_key(prop, lustre=True)))
        self.assertEqual([('Spec/Assertion1', 'falsified', None), ('_one_mode_active', 'valid', True)],
                         [(r['property'], r['verdict'], r.get('unmatched')) for r in records])


class TestReport(unittest.TestCase):

    def test_a_report(self):
        with tempfile.TemporaryDirectory() as folder:
            runs = os.path.join(folder, 'runs.jsonl')
            records = [
                {'kind': 'machine', 'date': '2026-10-31T10:00:00', 'cpu': 'a cpu', 'cores': 8, 'memory_gb': 16,
                 'platform': 'Linux', 'python': '3.12', 'intrepyd': '1.0.1', 'translator': '1.0.1',
                 'timeout': 120},
                {'kind': 'result', 'tool': 'intrepid', 'mode': 'float', 'model': 'm_SLDV', 'design': '7_ap',
                 'property': 'Spec/Assertion1', 'verdict': 'valid', 'time': 0.2, 'engine': 'br'},
                {'kind': 'result', 'tool': 'intrepid', 'mode': 'float', 'model': 'm_SLDV', 'design': '7_ap',
                 'property': 'Spec/Assertion3', 'verdict': 'falsified', 'time': 3.5, 'engine': 'bmc'},
                {'kind': 'result', 'tool': 'intrepid', 'mode': 'float', 'model': 'n_SLDV', 'design': '8_n',
                 'property': None, 'verdict': 'unsupported', 'error': 'n/Lookup: not supported yet'},
                {'kind': 'done', 'model': 'm_SLDV', 'mode': 'float'},
            ]
            with open(runs, 'w', encoding='utf-8') as out:
                out.write('\n'.join(json.dumps(r) for r in records) + '\n')
            reported = os.path.join(folder, 'reported.json')
            sldv = harness.parse_sldv_report(SLDV_REPORT)
            sldv['source'] = 'FRET_CoCoSim/7_ap/SLDV/sldv_output/m_SLDV/m_SLDV_report.html'
            other = {'model': 'n_SLDV', 'date': '2019-11-05T10:00:00', 'limit': 7200, 'source': 'x',
                     'objectives': [{'property': 'Spec/N1', 'verdict': 'undecided', 'time': None}]}
            kind2 = harness.parse_kind2_results(KIND2_RESULTS)
            kind2.update(source='FRET_CoCoSim/7_ap/CoCoSim/cocosim_output/m_PP/Kind2_results.xml',
                         design='7_ap', model='m_PP')
            with open(reported, 'w', encoding='utf-8') as out:
                json.dump({'suite_commit': harness.LM_COMMIT, 'sldv': [sldv, other], 'kind2': [kind2]}, out)
            out = os.path.join(folder, 'results', 'report.md')
            harness.report(SimpleNamespace(runs=[runs], reported=reported, out=out))
            with open(out, encoding='utf-8') as stream:
                text = stream.read()
        self.assertIn('| SLDV (reported) | 1 | 1 | 1 | 0 | 2 | 0 | 0 |', text)
        # 2 measured, and the one property of the model not translated
        self.assertIn('| intrepid, floats | 1 | 0 | 1 | 0 | 0 | 1 | 0 |', text)
        self.assertIn('| Spec/Assertion1 | valid (8.0 s) | valid (0.20 s) |', text)
        self.assertIn('| Spec/N1 | undecided | unsupported |', text)
        self.assertIn('not translated: n/Lookup: not supported yet', text)
        # valid under approximation for SLDV, falsified with floats
        self.assertIn('| m_SLDV | Spec/Assertion3 | SLDV (reported) | intrepid, floats |', text)
        self.assertIn('**m_PP**', text)
        self.assertIn('1 valid, 1 falsified, 1 undecided', text)
        self.assertIn('a cpu, 8 threads, 16 GB', text)

    def test_a_report_with_the_tools_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            machine = {'kind': 'machine', 'date': '2026-10-31T10:00:00', 'cpu': 'a cpu', 'cores': 8,
                       'memory_gb': 16, 'platform': 'Linux', 'timeout': 120}
            runs = {
                'intrepid.jsonl': [machine,
                                   {'kind': 'result', 'tool': 'intrepid', 'mode': 'real', 'model': 'm_SLDV',
                                    'design': '7_ap', 'property': 'Spec/Assertion1', 'verdict': 'valid', 'time': 0.2}],
                'sldv.jsonl': [dict(machine, matlab='matlab'),
                               {'kind': 'result', 'tool': 'sldv', 'mode': 'sldv', 'model': 'm_SLDV', 'design': '7_ap',
                                'property': 'Spec/Assertion1', 'verdict': 'falsified', 'time': 6,
                                'version': '3.5 (R2018a)'}],
                'kind2.jsonl': [dict(machine, kind2='kind2 v3.0.0'),
                                {'kind': 'result', 'tool': 'kind2', 'mode': 'kind2-2019', 'model': 'm_SLDV',
                                 'design': '7_ap', 'property': 'Spec/Assertion1', 'verdict': 'valid', 'time': 0.3,
                                 'kind2': 'v3.0.0'},
                                {'kind': 'result', 'tool': 'kind2', 'mode': 'kind2-2019', 'model': 'm_SLDV',
                                 'design': '7_ap', 'property': '_one_mode_active', 'verdict': 'valid', 'time': 0.1,
                                 'kind2': 'v3.0.0', 'unmatched': True}],
                'cocosim.jsonl': [machine,
                                  {'kind': 'result', 'tool': 'cocosim', 'mode': 'cocosim', 'model': 'm_SLDV',
                                   'design': '7_ap', 'property': None, 'verdict': 'unsupported',
                                   'error': 'Block X is not supported'}],
            }
            paths = []
            for name, records in runs.items():
                paths.append(os.path.join(folder, name))
                with open(paths[-1], 'w', encoding='utf-8') as out:
                    out.write('\n'.join(json.dumps(r) for r in records) + '\n')
            reported = os.path.join(folder, 'reported.json')
            with open(reported, 'w', encoding='utf-8') as out:
                json.dump({'suite_commit': harness.LM_COMMIT, 'sldv': [], 'kind2': []}, out)
            out = os.path.join(folder, 'report.md')
            harness.report(SimpleNamespace(runs=paths, reported=reported, out=out))
            with open(out, encoding='utf-8') as stream:
                text = stream.read()
        self.assertIn('| SLDV, measured | 0 | 0 | 1 | 0 | 0 | 0 | 0 |', text)
        self.assertIn('| Spec/Assertion1 | — | valid (0.20 s) | falsified (6.0 s) | unsupported | valid (0.30 s) |', text)
        self.assertIn("| m_SLDV | Spec/Assertion1 | intrepid, reals, Kind2 on CoCoSim's Lustre of 2019, measured "
                      '| SLDV, measured |', text)
        self.assertIn('CoCoSim and Kind2, measured: not translated: Block X is not supported', text)
        self.assertIn("properties naming no block of the SLDV model: `_one_mode_active` valid (0.10 s)", text)
        self.assertIn('SLDV 3.5 (R2018a)', text)


if __name__ == '__main__':
    unittest.main()
