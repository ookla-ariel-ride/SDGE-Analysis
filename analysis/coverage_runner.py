"""Local coverage gate: fresh measured census evidence and two bounded workers.

Only report tokens overlap the serial queue, in a guarded disposable snapshot.
Measurements are normalized while copied sources still exist, with content hashes
checked against the run snapshot. Nothing from a previous run is consumed.
"""
import argparse
import configparser
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
import threading
import uuid


SUITES = (
    'test_nbc_bill_reconciliation', 'test_rates', 'test_report_consistency',
    'test_tou_audit', 'test_parse_bills', 'test_carbon_fullyear',
    'test_carbon_dispatch_tradeoff', 'test_household', 'test_publish',
    'test_service_headroom', 'test_service_voltage', 'test_irreducible_bill',
    'test_privacy_tiers', 'test_bill_decomposition', 'test_rates_history',
    'test_tou_spread', 'test_scripts_runnable', 'test_nem3_grandfathering',
    'test_dsgs_vpp_backtest', 'test_cca_rate_extraction',
    'test_cca_bundled_counterfactual', 'test_battery_sizing_curve',
    'test_battery_dispatch_policies', 'test_perfect_foresight_dispatch',
    'test_tou_structure_stress', 'test_gross_import_decomposition',
    'test_reprice_by_vintage', 'test_uncertainty_propagation',
    'test_battery_backup_sims', 'test_deep_analyses', 'test_lifetime_payback',
    'test_soiling_analysis', 'test_extended_findings', 'test_battery_plan_matrix',
    'test_package_results', 'test_report_tokens', 'test_llm_providers',
    'test_egress_preflight', 'test_report_blocks', 'test_prose_blocks',
    'test_prose_lint', 'test_prose_rhythm', 'test_stamp_report_version',
    'test_generate_report', 'test_quiet_night_floor', 'test_heat_pump_conversion',
    'test_stage_private_data', 'test_extra_results', 'test_all_electric_endgame',
    'test_marginal_capacity_value', 'test_portable_solar_storage',
    'test_dry_run', 'test_private_egress', 'test_suite_runner',
    'test_coverage_runner',
)
REQUEST_ENV = 'SDGE_COVERAGE_REQUEST'


class GateError(RuntimeError):
    """Infrastructure or proof failure, distinct from a measured <90% verdict."""


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _tree(path, excluded=None):
    result = {}
    for base, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if Path(base, d).resolve() != excluded)
        for name in sorted(files):
            item = Path(base, name)
            if item.resolve() == excluded:
                continue
            result[str(item.relative_to(path))] = sha256(item)
    return result


def checked_run_dir(root, run_dir):
    """Refuse public destinations and private symlinks before creating logs."""
    root = Path(root).resolve()
    private = root / 'private'
    candidate = Path(run_dir).resolve()
    if private.resolve() != private or candidate.parent != private:
        raise GateError('coverage evidence must stay directly inside this checkout private directory')
    return candidate


def tracked_snapshot(root):
    # Minimal synthetic fixtures can lack Git; a real gate cannot, because
    # Sandbox.build independently requires a tracked checkout before any task.
    if not (root / '.git').exists():
        return {}
    from dry_run import tracked_files
    return {name: sha256(root / name) if (root / name).is_file() else None
            for name in tracked_files(root)}


def snapshot(root, run_dir):
    """Hash all analysis source, public artifacts, and private files except this run.

    Excluding precisely one fresh run directory lets logs grow without exempting
    the archive, another run, or unrelated private evidence from preservation.
    """
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    return {'tracked': tracked_snapshot(root),
            'source': {p.name: sha256(p) for p in sorted((root / 'analysis').glob('*.py'))},
            'data': _tree(root / 'data'),
            'private': _tree(root / 'private', run_dir),
            'config': sha256(root / '.coveragerc')}


def check_snapshot(root, run_dir, expected):
    if snapshot(root, run_dir) != expected:
        raise GateError('source/input/artifact snapshot changed; proof rejected')


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def inherited_fds(*extra):
    """Preserve the shell's directory lock as well as each sandbox marker."""
    fds = list(extra)
    # Gate-only children receive this explicit declaration. Standalone tests
    # must not accidentally pass an unrelated descriptor numbered 9.
    for key in ('SDGE_COVERAGE_LOCK_FD', 'SDGE_COVERAGE_MARKER_FD'):
        if os.environ.get(key):
            fds.append(int(os.environ[key]))
    for fd in fds:
        os.fstat(fd)  # A declared but closed lock is an infrastructure error.
    return tuple(sorted(set(fds)))


def clean_env():
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'COVERAGE_PROCESS_START', 'COVERAGE_FILE', REQUEST_ENV):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def coverage_config(root, destination, source_roots):
    """Retain all exclusions; add only explicit current-task source files."""
    config = configparser.ConfigParser(interpolation=None)
    config.read(Path(root) / '.coveragerc')
    config.set('run', 'parallel', 'false')
    config.set('run', 'include', '\n' + '\n'.join(
        str(p.resolve()) for source in source_roots for p in sorted(Path(source).glob('*.py'))))
    # Normalization below does stricter, hash-checked mapping than [paths].
    if config.has_section('paths'):
        config.remove_section('paths')
    with Path(destination).open('w') as stream:
        config.write(stream)


def normalize_measurement(raw, destination, root, expected, source_roots, target=None):
    from coverage import CoverageData
    try:
        if not Path(raw).is_file():
            raise GateError('coverage file is absent')
        original = CoverageData(basename=str(raw))
        original.read()
        mapped = {}
        allowed = {Path(p).resolve() for p in source_roots}
        for filename in original.measured_files():
            source = Path(filename).resolve()
            if source.parent not in allowed or source.name not in expected['source']:
                raise GateError('coverage contains an uncontrolled source path')
            if sha256(source) != expected['source'][source.name]:
                raise GateError('measured source differs from the accepted snapshot')
            canonical = str(Path(root).resolve() / 'analysis' / source.name)
            mapped.setdefault(canonical, set()).update(original.lines(filename) or ())
        if not any(mapped.values()):
            raise GateError('coverage contains no measured source lines')
        if target and not mapped.get(str(Path(root).resolve() / 'analysis' / target)):
            raise GateError('coverage does not measure the requested generator target')
        normalized = CoverageData(basename=str(destination))
        normalized.add_lines(mapped)
        normalized.write()
        return {name: expected['source'][Path(name).name] for name in sorted(mapped)}
    except GateError:
        raise
    except Exception as error:
        raise GateError(f'unusable coverage: {type(error).__name__}') from error


class Task:
    """One measured subprocess with private logs and a unique coverage destination."""
    def __init__(self, name, script, cwd, run_dir, root, expected,
                 source_roots=None, pass_fds=(), timeout=3600, target=None, env=None):
        self.name, self.script = name, Path(script).resolve()
        self.cwd, self.root, self.run_dir = Path(cwd).resolve(), Path(root).resolve(), Path(run_dir).resolve()
        self.expected = expected
        self.source_roots = tuple(source_roots or (self.root / 'analysis',))
        self.pass_fds, self.timeout, self.target = inherited_fds(*pass_fds), timeout, target
        self.env = clean_env()
        self.env.update(env or {})
        # coverage's -m entrypoint can preserve CWD on older Python. Give it
        # only this task's source directory, never inherited checkout paths.
        self.env['PYTHONPATH'] = str(self.script.parent)
        self.prefix = self.run_dir / (name.replace('.py', '') + '-' + uuid.uuid4().hex)
        self.log = self.prefix.with_suffix('.log')
        self.raw = self.prefix.with_suffix('.raw')
        self.measurement = self.prefix.with_suffix('.coverage')
        self.config = self.prefix.with_suffix('.rc')
        self.process = self.stream = None
        self.started = None
        self.record = None
        self.ended = None
        self.timed_out = False
        self.watcher = None

    def _watch(self):
        # Forward only the coordinator's bounded generator progress protocol.
        # All other stdout/stderr remains private, including household values.
        reader = self.log.open(errors='replace') if self.name == 'test_scripts_runnable' else None
        def progress():
            if reader is not None:
                for line in reader:
                    if re.match(r'^(?:start  [a-z0-9_]+\.py$|finish [a-z0-9_]+\.py (?:passed|failed) )', line):
                        print(line.rstrip(), flush=True)
        try:
            while self.process.poll() is None:
                progress()
                remaining = self.timeout - (time.monotonic() - self.started)
                if remaining <= 0:
                    self.timed_out = True
                    self.stop()
                    break
                try:
                    self.process.wait(timeout=min(.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            progress()
        finally:
            if reader is not None:
                reader.close()
            self.ended = time.monotonic()

    def start(self):
        checked_run_dir(self.root, self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        coverage_config(self.root, self.config, self.source_roots)
        self.env['COVERAGE_FILE'] = str(self.raw)
        self.stream = self.log.open('w')
        self.started = time.monotonic()
        print(f'start  {self.name}', flush=True)
        try:
            self.process = subprocess.Popen(
                [sys.executable, '-m', 'coverage', 'run', '--rcfile=' + str(self.config), str(self.script)],
                cwd=self.cwd, env=self.env, stdout=self.stream, stderr=subprocess.STDOUT,
                pass_fds=self.pass_fds, start_new_session=True)
            self.watcher = threading.Thread(target=self._watch, daemon=True)
            self.watcher.start()
        except BaseException as error:
            # The child exists before watcher construction/start. Every failure
            # from that point must stop and reap it before a sandbox can close.
            self.stop()
            try:
                self._record_result('startup failed: ' + type(error).__name__, {})
            except Exception:
                pass  # Recording trouble must not replace the startup exception.
            raise
        return self

    def stop(self):
        if self.process is not None:
            # A suite can have a living descendant even after its leader exits.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
            # Reap/stop the rest of this task's process group before disposal.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.stream is not None:
            self.stream.close()

    def _record_result(self, failure, sources):
        elapsed = (self.ended or time.monotonic()) - self.started
        self.record = {'name': self.name, 'elapsed': elapsed,
                       'exit': self.process.returncode if self.process is not None else None, 'status': 'failed' if failure else 'passed',
                       'log': str(self.log), 'coverage': str(self.measurement), 'sources': sources,
                       'coverage_mode': 'fresh-child', 'failure': failure}
        if not failure:
            self.record['coverage_sha256'] = sha256(self.measurement)
        write_json(self.prefix.with_suffix('.json'), self.record)
        print(f'finish {self.name} {self.record["status"]} {elapsed:.2f}s' +
              (f'; {failure}; log: {self.log}' if failure else ''), flush=True)

    def finish(self):
        if self.record is not None:
            if self.record['status'] != 'passed':
                raise GateError(f'{self.name}: {self.record["failure"]}; log: {self.log}')
            return self.record
        failure = None
        sources = {}
        try:
            if self.watcher is None or self.watcher.ident is None:
                raise GateError('worker startup did not complete')
            self.watcher.join()
            if self.timed_out:
                raise GateError('timed out')
            if self.process.returncode:
                cases = re.findall(r'^FAIL\s+(case_[A-Za-z0-9_]+):', self.log.read_text(errors='replace'), re.M)
                detail = '; checks: ' + ', '.join(cases[:3]) if cases else ''
                raise GateError(f'exit {self.process.returncode}' + detail)
            sources = normalize_measurement(self.raw, self.measurement, self.root,
                                            self.expected, self.source_roots, self.target)
        except subprocess.TimeoutExpired:
            failure = 'timed out'
        except GateError as error:
            failure = str(error)
        except BaseException:
            failure = 'interrupted'
            raise
        finally:
            self.stop()
            self._record_result(failure, sources)
        if failure:
            raise GateError(f'{self.name}: {failure}; log: {self.log}')
        return self.record


def schedule(token, serial, jobs):
    """Start the isolated token task first; never overlap two serial tasks."""
    rows = []
    active = None
    try:
        token.start()
        if jobs == 1:
            rows.append(token.finish())
        for task in serial:
            if jobs == 2 and token.process.poll() not in (None, 0):
                token.finish()
            active = task
            active.start()
            rows.append(active.finish())
            active = None
        if jobs == 2:
            rows.append(token.finish())
        return rows
    finally:
        for task in (active, token):
            if task is None or task.process is None:
                continue
            task.stop()
            if task.record is None:
                try:
                    task.finish()
                except GateError:
                    pass  # Preserve the original failure; keep this task's record.


def make_request(root, run_dir, names, owns):
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    checked_run_dir(root, run_dir)
    run_id = uuid.uuid4().hex
    request = {'run_id': run_id, 'root': str(root), 'run_dir': str(run_dir),
               'receipt': str(run_dir / (run_id + '-receipt.json')),
               'names': sorted(names), 'owns': {n: [list(x) for x in owns.get(n, ())] for n in sorted(names)},
               'snapshot': snapshot(root, run_dir)}
    write_json(run_dir / 'request.json', request)
    return request


class CensusMeasurement:
    """Collect the real census only; publish its receipt after cleanup succeeds."""
    def __init__(self, request):
        self.request = request
        self.root = Path(request['root'])
        self.run_dir = Path(request['run_dir'])
        self.records = []
        if Path(request['receipt']).exists():
            raise GateError('stale census receipt already exists')
        check_snapshot(self.root, self.run_dir, request['snapshot'])

    def begin(self, tmp, names):
        if sorted(names) != self.request['names']:
            raise GateError('wrong real census generator inventory')
        # Both flat and analysis copies must equal the accepted source snapshot.
        for folder in (tmp, tmp / 'analysis'):
            for name, digest in self.request['snapshot']['source'].items():
                if sha256(folder / name) != digest:
                    raise GateError('census copied source differs from snapshot')
        # Check every actual copied input, including public baseline artifacts.
        for name, digest in self.request['snapshot']['data'].items():
            if sha256(tmp / 'data' / name) != digest:
                raise GateError('census artifact baseline differs from snapshot')
        for rel, digest in _tree(tmp / 'private').items():
            if self.request['snapshot']['private'].get(rel) != digest:
                raise GateError('census private copy differs from snapshot')
        for name in ('usage.csv', 'samA.csv', 'samB.csv'):
            if (tmp / name).exists() and sha256(tmp / name) != self.request['snapshot']['private'].get('verify/' + name):
                raise GateError('census staged input differs from snapshot')

    def task(self, name, tmp, pass_fds):
        return Task(name, tmp / name, tmp, self.run_dir, self.root, self.request['snapshot'],
                    source_roots=(tmp, tmp / 'analysis'), pass_fds=pass_fds, timeout=900, target=name)

    def artifacts(self, name, tmp, before, record):
        outputs = []
        for where, filename in self.request['owns'][name]:
            path = tmp / filename if where == 'cwd' else tmp / 'data' / filename
            baseline = self.request['snapshot']['data'].get(filename)
            outputs.append({'where': where, 'name': filename,
                            'written': path.exists() and path.stat().st_mtime_ns != before[filename],
                            'sha256': sha256(path) if path.exists() else None, 'baseline': baseline})
        record['artifacts'] = outputs
        self.records.append(record)

    def commit(self):
        check_snapshot(self.root, self.run_dir, self.request['snapshot'])
        receipt = dict(self.request, records=self.records, cleanup=True)
        # Validate contents before publishing the sole success marker.
        _check_receipt(self.request, receipt)
        write_json(self.request['receipt'], receipt)


def _check_receipt(request, receipt):
    if any(receipt.get(k) != request[k] for k in ('run_id', 'root', 'run_dir', 'receipt', 'names', 'owns', 'snapshot')):
        raise GateError('wrong or stale census receipt')
    records = receipt.get('records', [])
    if receipt.get('cleanup') is not True or sorted(r['name'] for r in records) != request['names']:
        raise GateError('incomplete or duplicate census receipt')
    from coverage import CoverageData
    for row in records:
        measurement = Path(row['coverage'])
        if measurement.parent.resolve() != Path(request['run_dir']).resolve():
            raise GateError('coverage is outside this run')
        if row['exit'] != 0 or row['status'] != 'passed' or not measurement.is_file():
            raise GateError('missing successful census measurement')
        if sha256(measurement) != row['coverage_sha256']:
            raise GateError('census measurement changed')
        data = CoverageData(basename=str(measurement))
        data.read()
        target = str(Path(request['root']) / 'analysis' / row['name'])
        if not data.lines(target):
            raise GateError('census measurement lacks generator target')
        for source in data.measured_files():
            path = Path(source)
            if path.parent != Path(request['root']) / 'analysis' or row['sources'].get(source) != request['snapshot']['source'].get(path.name):
                raise GateError('census measurement has wrong source mapping')
        outputs = row.get('artifacts', [])
        if [[x['where'], x['name']] for x in outputs] != request['owns'][row['name']]:
            raise GateError('incomplete artifact receipt')
        for output in outputs:
            baseline = request['snapshot']['data'].get(output['name'])
            if not output['written'] or not output['sha256'] or output['baseline'] != baseline or (baseline and output['sha256'] != baseline):
                raise GateError('artifact was missing, unchanged, or different')
    return records


def accept_receipt(request):
    try:
        check_snapshot(request['root'], request['run_dir'], request['snapshot'])
        return _check_receipt(request, json.loads(Path(request['receipt']).read_text()))
    except GateError:
        raise
    except Exception as error:
        raise GateError(f'missing or unusable census receipt: {type(error).__name__}') from error


def measurement_union(root, records):
    """Recheck accepted bytes and canonical source lines immediately before reuse."""
    from coverage import CoverageData
    if not records:
        raise GateError('no accepted coverage records')
    union = {}
    seen = set()
    for row in records:
        if not isinstance(row, dict):
            raise GateError('coverage inputs need accepted task records')
        path = Path(row['coverage'])
        if str(path) in seen or row['exit'] != 0 or row['status'] != 'passed':
            raise GateError('duplicate or unsuccessful coverage input')
        seen.add(str(path))
        if not path.is_file() or sha256(path) != row['coverage_sha256']:
            raise GateError('accepted coverage file is missing or changed')
        data = CoverageData(basename=str(path))
        data.read()
        if set(data.measured_files()) != set(row['sources']):
            raise GateError('accepted coverage source inventory differs')
        if not any(data.lines(name) for name in data.measured_files()):
            raise GateError('accepted coverage has no measured lines')
        for name in data.measured_files():
            source = Path(name)
            if source.parent != Path(root).resolve() / 'analysis' or sha256(source) != row['sources'][name]:
                raise GateError('accepted coverage source changed or is uncontrolled')
            union.setdefault(name, set()).update(data.lines(name) or ())
        if sha256(path) != row['coverage_sha256']:
            raise GateError('accepted coverage changed while reading')
    return union


def final_report(root, measurement, records, coverage_command=None):
    """Combine exactly the accepted union. Exit 1 means a measured <90%."""
    from coverage import CoverageData
    env = clean_env()
    env['COVERAGE_FILE'] = str(measurement)
    command = coverage_command or [sys.executable, '-m', 'coverage']
    combine_log = Path(str(measurement) + '.combine.log')
    try:
        expected = measurement_union(root, records)
        result = subprocess.run(command + ['combine', '--keep'] + [r['coverage'] for r in records],
                                cwd=root, env=env, capture_output=True, text=True,
                                pass_fds=inherited_fds())
        combine_log.write_text(result.stdout + result.stderr)
        if result.returncode:
            print('check_coverage: coverage combine failed -- nothing was measured')
            print(result.stdout + result.stderr)
            return 2
        # combine can skip unreadable inputs with a warning and exit zero. Hash
        # checks on BOTH sides plus a combined file/line union check reject that.
        if measurement_union(root, records) != expected:
            raise GateError('accepted coverage union changed during combine')
        if not Path(measurement).is_file():
            raise GateError('combined coverage file is absent')
        combined = CoverageData(basename=str(measurement))
        combined.read()
        actual = {name: set(combined.lines(name) or ()) for name in combined.measured_files()}
        if actual != expected:
            raise GateError('combined coverage file/line union differs from accepted inputs')
    except Exception as error:
        with combine_log.open('a') as stream:
            stream.write(f'{type(error).__name__}: {error}\n')
        cause = str(error) if isinstance(error, GateError) else type(error).__name__
        print(f'check_coverage: unusable coverage: {cause}; log: {combine_log}')
        return 2
    result = subprocess.run(command + ['report', '--rcfile=' + str(Path(root) / '.coveragerc')],
                            cwd=root, env=env, capture_output=True, text=True,
                            pass_fds=inherited_fds())
    if result.returncode:
        print('check_coverage: coverage report failed -- nothing was measured')
        print(result.stdout + result.stderr)
        return 2
    report = result.stdout
    print(report, end='')
    totals = [line.split() for line in report.splitlines() if line.startswith('TOTAL ')]
    if len(totals) != 1 or not totals[0][1].isdigit():
        print('check_coverage: no TOTAL row -- nothing was measured')
        return 2
    print(f'measured {totals[0][1]} statements from {measurement}')
    result = subprocess.run(command + ['report', '--rcfile=' + str(Path(root) / '.coveragerc'), '--fail-under=90'],
                            cwd=root, env=env, stdout=subprocess.DEVNULL, pass_fds=inherited_fds())
    if result.returncode not in (0, 2):
        raise GateError('coverage threshold command failed')
    print('COVERAGE GATE: ' + ('PASS (>= 90%)' if result.returncode == 0 else 'FAIL (< 90%)'))
    return 0 if result.returncode == 0 else 1


def main(argv=None):
    try:
        return _run_gate(argv)
    except (Exception, KeyboardInterrupt) as error:
        print(f'check_coverage: infrastructure failure: {type(error).__name__}: {error}', flush=True)
        return 2


def _run_gate(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    def interrupt(signum, frame):
        raise KeyboardInterrupt('signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupt)
    import dry_run
    import test_scripts_runnable as census
    root = Path(__file__).resolve().parent.parent
    run_dir = checked_run_dir(root, root / 'private' / ('verify-coverage-' + uuid.uuid4().hex))
    run_dir.mkdir(mode=0o700)
    if os.environ.get('SDGE_COVERAGE_LOCK_FD'):
        (root / '.check_coverage.holder').write_text(
            f'pid={os.getpid()} started={time.strftime("%Y-%m-%dT%H:%M:%S%z")} '
            f'data={run_dir / ".coverage"}\n')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    before = snapshot(root, run_dir)
    started = time.monotonic()
    sandbox = dry_run.Sandbox(root)
    rows = []
    result = 2
    try:
        # Complete this copy before any suite can mutate checkout or Git state.
        sandbox.build(extra_files=('analysis/coverage_runner.py', 'analysis/test_coverage_runner.py'))
        check_snapshot(root, run_dir, before)
        for name, digest in before['tracked'].items():
            copied = sandbox.path / name
            if (sha256(copied) if copied.is_file() else None) != digest:
                raise GateError('token snapshot tracked inputs differ')
        for name, digest in before['source'].items():
            if sha256(sandbox.path / 'analysis' / name) != digest:
                raise GateError('token snapshot source differs')
        if _tree(sandbox.path / 'data') != before['data']:
            raise GateError('token snapshot artifacts differ')
        copied_private = _tree(sandbox.path / 'private')
        if any(before['private'].get(name) != digest for name, digest in copied_private.items()):
            raise GateError('token snapshot private inputs differ')
        for name in dry_run.CWD_FIXTURES:
            if (sandbox.path / name).exists() and sha256(sandbox.path / name) != before['private'].get('verify/' + name):
                raise GateError('token snapshot staged inputs differ')
        marker = inherited_fds(sandbox._marker_fd.fileno())
        token = Task('test_report_tokens', sandbox.path / 'analysis/test_report_tokens.py', sandbox.path,
                     run_dir, root, before, source_roots=(sandbox.path / 'analysis',), pass_fds=marker,
                     env={'SDGE_COVERAGE_MARKER_FD': str(sandbox._marker_fd.fileno())})
        names = sorted(set(census.NEEDS_PRIVATE_ARCHIVE) | census.CI_RUNNABLE)
        request = make_request(root, run_dir, names, census.OWNS)
        tasks = [Task(name, root / 'analysis' / (name + '.py'), root, run_dir, root, before,
                      env={REQUEST_ENV: str(run_dir / 'request.json')} if name == 'test_scripts_runnable' else {})
                 for name in SUITES if name != 'test_report_tokens']
        rows = schedule(token, tasks, args.jobs)
        if _tree(sandbox.path / 'private') != copied_private:
            raise GateError('token worker changed its private input copy')
        records = accept_receipt(request)
        rows.extend(records)
        # The original direct loop also executed this library. It is not one
        # of the census's 40 generators, so it retains a distinct execution.
        rows.append(Task('billing_model_nem.py', sandbox.path / 'analysis/billing_model_nem.py', sandbox.path,
                         run_dir, root, before, source_roots=(sandbox.path / 'analysis',), pass_fds=marker,
                         target='billing_model_nem.py').start().finish())
        check_snapshot(root, run_dir, before)
        sandbox.dispose()  # A failed cleanup must precede and prevent the verdict.
        result = 0  # Cleanup and final preservation checks still precede the verdict.
    except (Exception, KeyboardInterrupt) as error:
        print(f'check_coverage: {type(error).__name__}: {error}; evidence: {run_dir}', flush=True)
    finally:
        if sandbox.path is not None:
            try:
                sandbox.dispose()
            except Exception as error:
                print(f'check_coverage: cleanup failed: {error}; evidence: {run_dir}', flush=True)
                result = 2
        try:
            check_snapshot(root, run_dir, before)
        except GateError as error:
            print(f'check_coverage: {error}; evidence: {run_dir}', flush=True)
            result = 2
    if result == 0:
        try:
            result = final_report(root, run_dir / '.coverage', rows)
        except (Exception, KeyboardInterrupt) as error:
            print(f'check_coverage: report failed: {error}; evidence: {run_dir}', flush=True)
            result = 2
    # Include completed and stopped tasks even when schedule() raised before
    # returning. These records come only from the fresh run, never old evidence.
    timings = []
    for path in sorted(run_dir.glob('*.json')):
        entry = json.loads(path.read_text())
        if 'status' in entry and 'elapsed' in entry:
            timings.append(entry)
    write_json(run_dir / 'timings.json', {'workers': args.jobs, 'elapsed': time.monotonic() - started,
               'exit': result, 'snapshot': before, 'tasks': timings})
    return result


if __name__ == '__main__':
    sys.exit(main())
