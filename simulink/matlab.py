"""
What the Simulink benchmark needs of MATLAB: CoCoSim, fetched and set up
as its own installer does, and the runs of MATLAB that measure SLDV and
CoCoSim, with the scripts of simulink/matlab/.

MATLAB runs without its desktop but with a display: some models of the
suite do not load with -nodisplay. Each run is in a folder of its own, where
MATLAB writes what it makes (slprj/, sldv_output/, CoCoSim's Lustre).
"""

import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(HERE, 'matlab')
COCOSIM = os.path.join(ROOT, 'third_party', 'cocosim')

# CoCoSim (NASA, NOSA 1.3) and the two repositories its installer
# (scripts/install_cocosim_lib.m) copies files from, at fixed commits
COCOSIM_URL = 'https://github.com/NASA-SW-VnV/CoCoSim.git'
COCOSIM_COMMIT = 'ebb91b7dfbc52bfe5074766281fd3fb8446976c9'
COCOSIM2_URL = 'https://github.com/coco-team/cocoSim2.git'
COCOSIM2_COMMIT = '8f162445dfc03c6c8f787830253c9ca06b133726'
EXTERNAL_URL = 'https://github.com/hbourbouh/cocosim-external-libs.git'
EXTERNAL_COMMIT = '411bca2136e3dfa3a4c00847ebd1daad36bf0acd'

# (from cocoSim2, to CoCoSim), as install_cocosim_lib.m copies them
COCOSIM2_FILES = [
    ('doc/installation.md', 'doc/cocosim_iowa_doc/installation.md'),
    ('doc/specificationLibrary.md', 'doc/cocosim_iowa_doc/specificationLibrary.md'),
    ('examples/contract', 'examples/contract'),
    ('libs', 'libs'),
    ('src/gui', 'src/external/cocosim_iowa/gui'),
    ('src/miscellaneous/MiscellaneousMenu.m', 'src/external/cocosim_iowa/miscellaneous/MiscellaneousMenu.m'),
    ('src/preferences', 'src/external/cocosim_iowa/preferences'),
    ('src/utils', 'src/external/cocosim_iowa/utils'),
    ('LICENSE', 'src/external/cocosim_iowa/LICENSE'),
    ('src/frontEnd/pp', 'src/frontEnd/pp/std_pp'),
    ('src/frontEnd/IR', 'src/frontEnd/IR/std_IR'),
    ('src/middleEnd/cocoSpecCompiler', 'src/middleEnd/iowa_toLustre'),
    ('src/backEnd/common', 'src/backEnd/common'),
    ('src/backEnd/templates', 'src/backEnd/templates'),
    ('src/backEnd/verification/cocoSpecVerify', 'src/backEnd/verification/cocoSpecVerify'),
    ('src/backEnd/verification/VerificationMenu.m', 'src/backEnd/verification/VerificationMenu.m'),
]
# ...which then removes these
COCOSIM2_REMOVED = ['src/frontEnd/pp/std_pp/cocosim_pp.m', 'libs/contract/slblocks.m']
EXTERNAL_FILES = [
    ('+AutoLayout', 'src/external/+external_lib/+AutoLayout'),
    ('+cmd_timeout', 'src/external/+external_lib/+cmd_timeout'),
    ('html_lib', 'src/external/html_lib'),
    ('jkind', 'tools/verifiers/jkind'),
]
# marks a CoCoSim set up by fetch_cocosim
SETUP_MARK = '.intrepid-benchmarks-setup'


def _git(*arguments, cwd=None):
    return subprocess.run(['git'] + list(arguments), cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _clone(url, commit, into):
    """into at commit: cloned, or checked"""
    if os.path.isdir(os.path.join(into, '.git')):
        head = _git('rev-parse', 'HEAD', cwd=into)
        if head != commit:
            raise SystemExit(f'{into} is at {head[:7]}, not {commit[:7]}: remove it, or check out {commit}')
        return False
    os.makedirs(into, exist_ok=True)
    _git('init', '-q', cwd=into)
    _git('remote', 'add', 'origin', url, cwd=into)
    _git('fetch', '-q', '--depth', '1', 'origin', commit, cwd=into)
    _git('checkout', '-q', 'FETCH_HEAD', cwd=into)
    return True


def _copy(source, destination):
    """As MATLAB's copyfile: a folder's content into a folder, a file to a file"""
    if os.path.isdir(source):
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)


def fetch_cocosim(into=COCOSIM):
    """CoCoSim at COCOSIM_COMMIT in into, with the files of its external
    libraries copied in as its installer does, without MATLAB. The model
    checkers are not installed in it: the runs give it Kind2 and z3."""
    _clone(COCOSIM_URL, COCOSIM_COMMIT, into)
    if os.path.exists(os.path.join(into, SETUP_MARK)):
        print(f'{into}: CoCoSim at {COCOSIM_COMMIT[:7]}, set up already')
        return
    build = os.path.join(into, 'tools', 'build', 'github')
    cocosim2 = os.path.join(build, 'cocosim')
    external = os.path.join(build, 'externalLib')
    _clone(COCOSIM2_URL, COCOSIM2_COMMIT, cocosim2)
    _clone(EXTERNAL_URL, EXTERNAL_COMMIT, external)
    for source, destination in COCOSIM2_FILES:
        _copy(os.path.join(cocosim2, source), os.path.join(into, destination))
    for path in COCOSIM2_REMOVED:
        if os.path.exists(os.path.join(into, path)):
            os.remove(os.path.join(into, path))
    for source, destination in EXTERNAL_FILES:
        _copy(os.path.join(external, source), os.path.join(into, destination))
    with open(os.path.join(into, SETUP_MARK), 'w', encoding='utf-8') as mark:
        mark.write(f'cocosim {COCOSIM_COMMIT}\ncocoSim2 {COCOSIM2_COMMIT}\nexternal libs {EXTERNAL_COMMIT}\n')
    print(f'{into}: CoCoSim at {COCOSIM_COMMIT[:7]}, with cocoSim2 at {COCOSIM2_COMMIT[:7]} '
          f'and its external libraries at {EXTERNAL_COMMIT[:7]}')


def matlab_string(text):
    return "'" + text.replace("'", "''") + "'"


def run_matlab(matlab, statement, folder, timeout, log):
    """Runs statement in MATLAB, in folder, its output in log; the exit status,
    or None if it did not end within timeout seconds"""
    command = [matlab, '-nosplash', '-nodesktop', '-logfile', log, '-r',
               f'try, addpath({matlab_string(SCRIPTS)}); {statement}; '
               'catch e, disp(getReport(e)); end; exit']
    with subprocess.Popen(command, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.STDOUT, start_new_session=True) as process:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, 9)
            process.wait()
            return None


def scratch(prefix):
    return tempfile.mkdtemp(prefix=prefix)
