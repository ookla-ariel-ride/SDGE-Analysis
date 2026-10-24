"""Small real-process checks for the local coverage coordinator.

These fixtures never use the household archive. They exercise coverage databases,
output writes, snapshot preservation, scheduling and inherited kernel locks.
"""
import ast
import contextlib
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import coverage_runner as runner
except ImportError:
    runner = None


class CoverageRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runner, "the measured coordinator is not implemented")
        # Synthetic tasks must not borrow the outer gate's actual receipt/locks.
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("SDGE_COVERAGE_REQUEST", "SDGE_COVERAGE_LOCK_FD", "SDGE_COVERAGE_MARKER_FD"):
            os.environ.pop(key, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Path(self.tmp.name).resolve() / "repo"
        for name in ("analysis", "data", "private/verify", "private/verify-run"):
            (self.fixture / name).mkdir(parents=True, exist_ok=True)
        self.run = self.fixture / "private/verify-run"
        self.script = self.fixture / "analysis/example.py"
        self.script.write_text("from pathlib import Path\nPath('data/result.txt').write_text('expected\\n')\n")
        (self.fixture / "data/result.txt").write_text("expected\n")
        (self.fixture / "private/verify/usage.csv").write_text("invented\n")
        (self.fixture / ".coveragerc").write_text(
            "[run]\nparallel = true\ninclude =\n    */analysis/*.py\n"
            "    */private/verify/*.py\nomit =\n    */test_*.py\n"
            "    */carbon_timing.py\n    */pvoutput_*.py\n[report]\nprecision = 1\n")

    def snapshot(self):
        return runner.snapshot(self.fixture, self.run)

    def task(self, script=None, cwd=None, **kwargs):
        return runner.Task("example", script or self.script, cwd or self.fixture,
                           self.run, self.fixture, self.snapshot(), **kwargs)

    def test_measured_reproduction_maps_flat_and_analysis_copies_once(self):
        self.script.write_text("import helper\nfrom analysis import helper as copied_helper\nfrom pathlib import Path\n"
                               "Path('data/result.txt').write_text('expected\\n')\n")
        (self.fixture / "analysis/helper.py").write_text("VALUE = 42\n")
        copy_root = Path(self.tmp.name) / "copy"
        shutil.copytree(self.fixture, copy_root)
        shutil.copy(self.script, copy_root / "example.py")
        shutil.copy(self.fixture / "analysis/helper.py", copy_root / "helper.py")
        task = self.task(script=copy_root / "example.py", cwd=copy_root,
                         source_roots=(copy_root, copy_root / "analysis"), target="example.py")
        record = task.start().finish()
        self.assertEqual(record["exit"], 0)
        self.assertGreaterEqual(record["elapsed"], 0)
        self.assertEqual((copy_root / "data/result.txt").read_text(), "expected\n")
        from coverage import CoverageData
        data = CoverageData(basename=record["coverage"])
        data.read()
        self.assertEqual(set(data.measured_files()),
                         {str(self.script), str(self.fixture / "analysis/helper.py")})
        self.assertTrue(data.lines(str(self.script)))

    def test_failed_and_timed_out_children_keep_private_diagnostics(self):
        for code, timeout, cause in (("raise RuntimeError('invented diagnostic')\n", 5, "exit"),
                                     ("import time\ntime.sleep(20)\n", .1, "timed out")):
            self.script.write_text(code)
            with self.assertRaisesRegex(runner.GateError, cause) as failure:
                self.task(timeout=timeout).start().finish()
            self.assertIn(str(self.run), str(failure.exception))
        self.assertTrue(any("invented diagnostic" in p.read_text() for p in self.run.glob("*.log")))

    def test_exit_zero_without_measurement_fails(self):
        self.script.write_text("import os\nos._exit(0)\n")
        with self.assertRaisesRegex(runner.GateError, "coverage"):
            self.task().start().finish()

    def test_imports_without_measured_target_do_not_prove_generator(self):
        self.script.write_text("VALUE = 1\n")
        test_script = self.fixture / "analysis/test_driver.py"
        test_script.write_text("import example\n")
        with self.assertRaisesRegex(runner.GateError, "target"):
            self.task(script=test_script, target="missing.py").start().finish()

    def test_changed_copy_is_not_mapped_to_canonical_source(self):
        copy_root = Path(self.tmp.name) / "copy"
        copy_root.mkdir()
        changed = copy_root / "example.py"
        changed.write_text("VALUE = 'different'\n")
        with self.assertRaisesRegex(runner.GateError, "source"):
            self.task(script=changed, cwd=copy_root, source_roots=(copy_root,)).start().finish()

    def test_snapshot_detects_source_input_and_artifact_changes(self):
        for path in (self.script, self.fixture / "private/verify/usage.csv", self.fixture / "data/result.txt"):
            before = self.snapshot()
            original = path.read_bytes()
            path.write_bytes(original + b"changed\n")
            with self.assertRaisesRegex(runner.GateError, "snapshot"):
                runner.check_snapshot(self.fixture, self.run, before)
            path.write_bytes(original)
        before = self.snapshot()
        (self.run / "owned.log").write_text("new log\n")
        runner.check_snapshot(self.fixture, self.run, before)
        (self.fixture / "private/unowned.log").write_text("outside run\n")
        with self.assertRaises(runner.GateError):
            runner.check_snapshot(self.fixture, self.run, before)

    def test_tracked_non_python_inputs_and_inventory_are_bound_to_snapshot(self):
        (self.fixture / '.gitignore').write_text('private/\n__pycache__/\n')
        inputs = [self.fixture / name for name in ('report-template.html', 'stage-private-data.sh')]
        for path in inputs:
            path.write_text('original\n')
        subprocess.run(['git', 'init', '-q', str(self.fixture)], check=True)
        subprocess.run(['git', '-C', str(self.fixture), 'add', '.'], check=True)
        before = self.snapshot()
        for path in inputs:
            path.write_text('changed\n')
            with self.assertRaisesRegex(runner.GateError, 'snapshot'):
                runner.check_snapshot(self.fixture, self.run, before)
            path.write_text('original\n')
        extra = self.fixture / 'new-input.txt'
        extra.write_text('new\n')
        subprocess.run(['git', '-C', str(self.fixture), 'add', 'new-input.txt'], check=True)
        with self.assertRaisesRegex(runner.GateError, 'snapshot'):
            runner.check_snapshot(self.fixture, self.run, before)

    def census(self, code=None):
        import test_scripts_runnable as census
        if code is not None:
            self.script.write_text(code)
        request = runner.make_request(self.fixture, self.run, ["example.py"],
                                      {"example.py": [("data", "result.txt")]})
        measurement = runner.CensusMeasurement(request)
        with patch.multiple(census, ROOT=self.fixture, ANALYSIS=self.fixture / "analysis",
                            SANDBOX=self.fixture / "private/verify", OWNS={"example.py": [("data", "result.txt")]}):
            failures = census._run_generators(["example.py"], False,
                inspect=lambda tmp: [] if (tmp / "data/result.txt").read_bytes() == b"expected\n" else ["changed output"],
                measurement=measurement)
        return request, failures

    def test_successful_real_census_receipt_survives_sandbox_cleanup(self):
        request, failures = self.census()
        self.assertEqual(failures, [])
        records = runner.accept_receipt(request)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "example.py")
        self.assertTrue(Path(records[0]["coverage"]).exists())

    def test_changed_missing_and_noop_outputs_cannot_supply_receipt(self):
        for code in ("from pathlib import Path\nPath('data/result.txt').write_text('wrong')\n",
                     "from pathlib import Path\nPath('data/result.txt').unlink()\n",
                     "VALUE = 1\n"):
            request, failures = self.census(code)
            self.assertTrue(failures)
            with self.assertRaises(runner.GateError):
                runner.accept_receipt(request)

    def test_missing_wrong_duplicate_stale_and_unmeasured_receipts_fail(self):
        request, failures = self.census()
        self.assertFalse(failures)
        path = Path(request["receipt"])
        good = json.loads(path.read_text())
        for mutate in (lambda x: x.update(run_id="other-run"),
                       lambda x: x["records"].append(x["records"][0]),
                       lambda x: x.update(snapshot={}),
                       lambda x: x["records"][0].update(name="other.py"),
                       lambda x: x["records"][0].update(coverage=str(self.run / "absent")),
                       lambda x: x["records"][0].update(artifacts=[])):
            bad = copy.deepcopy(good)
            mutate(bad)
            path.write_text(json.dumps(bad))
            with self.assertRaises(runner.GateError):
                runner.accept_receipt(request)
        path.unlink()
        with self.assertRaises(runner.GateError):
            runner.accept_receipt(request)

    def test_cleanup_failure_never_commits_receipt(self):
        import test_scripts_runnable as census
        owned = []
        original = census.tempfile.TemporaryDirectory
        def temporary(*args, **kwargs):
            temp = original(*args, **kwargs)
            owned.append(temp)
            return temp
        try:
            with patch.object(census.tempfile, "TemporaryDirectory", side_effect=temporary), \
                 patch.object(census.shutil, "rmtree", side_effect=OSError("injected cleanup")):
                with self.assertRaisesRegex(RuntimeError, "could not be removed"):
                    self.census()
            self.assertFalse(list(self.run.glob("*receipt.json")))
        finally:
            for temp in owned:
                if Path(temp.name).exists():
                    shutil.rmtree(temp.name)

    def test_one_two_worker_parity_and_serial_remainder(self):
        events = self.fixture / "events"
        scripts = []
        for name in ("token_worker", "unsafe_a", "unsafe_b"):
            p = self.fixture / "analysis" / (name + ".py")
            p.write_text("import time\nfrom pathlib import Path\n"
                         f"p = Path({str(events)!r})\n"
                         f"with p.open('a') as f: f.write('{name} start\\n')\n"
                         "time.sleep(.15)\n"
                         f"with p.open('a') as f: f.write('{name} end\\n')\n")
            scripts.append(p)
        for jobs in (1, 2):
            events.write_text("")
            tasks = [runner.Task(p.stem, p, self.fixture, self.run, self.fixture, self.snapshot()) for p in scripts]
            rows = runner.schedule(tasks[0], tasks[1:], jobs)
            self.assertEqual({r["name"] for r in rows}, {"token_worker", "unsafe_a", "unsafe_b"})
            lines = events.read_text().splitlines()
            self.assertLess(lines.index("unsafe_a end"), lines.index("unsafe_b start"))
            if jobs == 2:
                self.assertLess(lines.index("unsafe_a start"), lines.index("token_worker end"))
            else:
                self.assertLess(lines.index("token_worker end"), lines.index("unsafe_a start"))

    def test_valid_plus_corrupt_measurement_never_reaches_passing_verdict(self):
        record = self.task().start().finish()
        corrupt = self.run / 'corrupt.coverage'
        corrupt.write_text('not a coverage database\n')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = runner.final_report(self.fixture, self.run / '.coverage',
                                         [record, dict(record, coverage=str(corrupt), coverage_sha256=runner.sha256(corrupt))])
        self.assertEqual(result, 2)
        self.assertNotIn('COVERAGE GATE: PASS', output.getvalue())

    def test_final_report_binds_accepted_hash_and_combined_line_union(self):
        from coverage import CoverageData
        record = self.task().start().finish()
        # The accepted record is a real Task result, not a hand-built receipt.
        with contextlib.redirect_stdout(io.StringIO()):
            healthy = runner.final_report(self.fixture, self.run / '.healthy', [record])
        self.assertEqual(healthy, 0)
        data = CoverageData(basename=record['coverage'])
        data.read()
        data.add_lines({str(self.script): {999}})
        data.write()
        with contextlib.redirect_stdout(io.StringIO()):
            changed = runner.final_report(self.fixture, self.run / '.changed', [record])
        self.assertEqual(changed, 2)

    def test_combine_that_drops_measured_lines_fails_closed(self):
        record = self.task().start().finish()
        command = self.fixture / 'drop_lines.py'
        command.write_text("import os,subprocess,sys\nfrom coverage import CoverageData\n"
            "result=subprocess.run([sys.executable,'-m','coverage',*sys.argv[1:]])\n"
            "if sys.argv[1]=='combine' and result.returncode==0:\n"
            " d=CoverageData(basename=os.environ['COVERAGE_FILE']); d.read()\n"
            " rows={p:set(d.lines(p) or []) for p in d.measured_files()}\n"
            " for lines in rows.values():\n"
            "  if lines: lines.remove(min(lines)); break\n"
            " d.erase(); d.add_lines(rows); d.write()\n"
            "sys.exit(result.returncode)\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = runner.final_report(self.fixture, self.run / '.dropped', [record],
                                         [sys.executable, str(command)])
        self.assertEqual(result, 2)
        self.assertNotIn('COVERAGE GATE: PASS', output.getvalue())

    def test_watcher_start_failure_reaps_child_and_preserves_original_error(self):
        self.script.write_text('import time\ntime.sleep(30)\n')
        for scheduled in (False, True):
            task = self.task()
            try:
                with patch.object(runner.threading.Thread, 'start',
                                  side_effect=RuntimeError('injected watcher startup failure')):
                    with self.assertRaisesRegex(RuntimeError, 'injected watcher startup failure'):
                        if scheduled:
                            runner.schedule(task, [], 2)
                        else:
                            task.start()
                self.assertIsNotNone(task.process.poll(), 'startup left a child alive')
                self.assertEqual(task.record['status'], 'failed')
                self.assertGreaterEqual(task.record['elapsed'], 0)
                self.assertTrue(Path(task.record['log']).is_file())
            finally:
                task.stop()

    def test_serial_failure_stops_and_records_active_token_worker(self):
        slow = self.fixture / 'analysis/slow.py'
        slow.write_text('import time\ntime.sleep(30)\n')
        broken = self.fixture / 'analysis/broken.py'
        broken.write_text("raise RuntimeError('fixture')\n")
        token = self.task(script=slow)
        serial = runner.Task('broken', broken, self.fixture, self.run, self.fixture, self.snapshot())
        with self.assertRaises(runner.GateError):
            runner.schedule(token, [serial], 2)
        self.assertIsNotNone(token.process.returncode)
        self.assertEqual(token.record['status'], 'failed')
        self.assertEqual(serial.record['status'], 'failed')
        with self.assertRaises(ProcessLookupError):
            os.kill(token.process.pid, 0)

    def test_gate_census_cannot_skip_missing_archive(self):
        import test_scripts_runnable as census
        request = runner.make_request(self.fixture, self.run, ['example.py'],
                                      {'example.py': [('data', 'result.txt')]})
        (self.fixture / 'private/verify/usage.csv').unlink()
        # The expected input is absent from the start, not changed after proof.
        request['snapshot'] = self.snapshot()
        runner.write_json(self.run / 'request.json', request)
        with patch.dict(os.environ, {runner.REQUEST_ENV: str(self.run / 'request.json')}), \
             patch.object(census, 'SANDBOX', self.fixture / 'private/verify'):
            with self.assertRaisesRegex(AssertionError, 'cannot skip'):
                census.case_generators_run_on_the_real_archive()

    def test_complete_coordinator_uses_isolated_token_copy_and_same_run_census(self):
        import test_scripts_runnable as census
        renamed = self.script.with_name("example3.py")
        self.script.rename(renamed)
        self.script = renamed
        # A real tiny Git checkout lets Sandbox.build exercise tracked copying.
        shutil.copy(Path(runner.__file__), self.fixture / 'analysis/coverage_runner.py')
        shutil.copy(Path(census.__file__), self.fixture / 'analysis/census_impl.py')
        for name in ('behavior_rebuild.py', 'suite_runner.py', 'rates.py', 'dry_run.py'):
            shutil.copy(Path(census.__file__).parent / name, self.fixture / 'analysis' / name)
        (self.fixture / 'analysis/helper.py').write_text('VALUE = 42\n')
        (self.fixture / 'analysis/billing_model_nem.py').write_text('import helper\nVALUE = helper.VALUE\n')
        (self.fixture / 'analysis/test_report_tokens.py').write_text(
            "import helper\nfrom pathlib import Path\n"
            f"assert Path(__file__).resolve().parent != Path({str(self.fixture / 'analysis')!r})\n")
        (self.fixture / 'analysis/test_scripts_runnable.py').write_text(
            "import census_impl as c\n"
            "c.CI_RUNNABLE={'example3.py'}; c.NEEDS_PRIVATE_ARCHIVE={}\n"
            "c.OWNS={'example3.py': [('data', 'result.txt')]}\n"
            "c.case_generators_run_on_the_real_archive()\n")
        (self.fixture / '.gitignore').write_text('private/\n__pycache__/\n')
        subprocess.run(['git', 'init', '-q', str(self.fixture)], check=True)
        subprocess.run(['git', '-C', str(self.fixture), 'add', '.'], check=True)
        original = self.snapshot()
        with patch.object(runner, '__file__', str(self.fixture / 'analysis/coverage_runner.py')), \
             patch.object(runner, 'SUITES', ('test_report_tokens', 'test_scripts_runnable')), \
             patch.multiple(census, CI_RUNNABLE={'example3.py'}, NEEDS_PRIVATE_ARCHIVE={},
                            OWNS={'example3.py': [('data', 'result.txt')]}):
            # The full census helper is deliberately much larger than this tiny
            # fixture exercises. It must reach a real below-floor verdict, not 2.
            progress = io.StringIO()
            with contextlib.redirect_stdout(progress):
                result = runner.main(['--jobs', '2'])
        self.assertIn('start  example3.py', progress.getvalue())
        self.assertIn('finish example3.py passed', progress.getvalue())
        self.assertEqual(result, 1)
        evidence = list((self.fixture / 'private').glob('verify-coverage-*/timings.json'))
        self.assertEqual(len(evidence), 1)
        report = json.loads(evidence[0].read_text())
        self.assertEqual(report['workers'], 2)
        self.assertEqual({r['name'] for r in report['tasks']},
                         {'test_report_tokens', 'test_scripts_runnable', 'example3.py', 'billing_model_nem.py'})
        self.assertTrue(all(r['exit'] == 0 for r in report['tasks']))
        self.assertEqual(original['source'], self.snapshot()['source'])
        self.assertEqual(original['data'], self.snapshot()['data'])
        self.assertEqual((self.fixture / 'private/verify/usage.csv').read_text(), 'invented\n')

    def test_unsafe_private_root_is_an_infrastructure_failure_before_any_task(self):
        outside = Path(self.tmp.name).resolve() / 'public'
        outside.mkdir()
        (self.fixture / 'private').rename(self.fixture / 'saved-private')
        (self.fixture / 'private').symlink_to(outside, target_is_directory=True)
        with patch.object(runner, '__file__', str(self.fixture / 'analysis/coverage_runner.py')):
            with contextlib.redirect_stdout(io.StringIO()):
                result = runner.main(['--jobs', '1'])
        self.assertEqual(result, 2)
        self.assertFalse(list(outside.iterdir()))

    def test_declared_but_closed_gate_lock_fails(self):
        with patch.dict(os.environ, {"SDGE_COVERAGE_LOCK_FD": "99999"}):
            with self.assertRaises(OSError):
                runner.inherited_fds()
        with patch.dict(os.environ, {"SDGE_COVERAGE_MARKER_FD": "99999"}):
            with self.assertRaises(OSError):
                runner.inherited_fds()

    def test_token_optimized_child_keeps_snapshot_and_outer_locks_after_parent_death(self):
        # Execute the actual helper's AST in a miniature snapshot. Importing the
        # whole token suite would load unrelated household artifacts into this fixture.
        source = (Path(__file__).parent / 'test_report_tokens.py').read_text()
        helper = next(n for n in ast.parse(source).body
                      if isinstance(n, ast.FunctionDef) and n.name == '_report_tokens_under_O')
        helper_file = self.fixture / 'analysis/helper_function.py'
        helper_file.write_text(ast.unparse(helper))
        shutil.copy(Path(runner.__file__), self.fixture / 'analysis/coverage_runner.py')
        locks = [self.fixture / 'outer', self.fixture / 'marker']
        ready = self.fixture / 'optimized-ready'
        driver = self.fixture / 'driver.py'
        child_code = ("import os,time\nfrom pathlib import Path\n"
                      f"Path({str(ready)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n")
        driver.write_text("import os,sys,fcntl,tempfile,pathlib,subprocess,types\n"
            f"sys.path.insert(0,{str(self.fixture / 'analysis')!r})\n"
            f"rt=types.SimpleNamespace(__file__={str(self.fixture / 'analysis/report_tokens.py')!r})\n"
            f"exec(pathlib.Path({str(helper_file)!r}).read_text())\n"
            f"files=[open(p,'w') for p in {list(map(str, locks))!r}]\n"
            "for f in files: fcntl.flock(f,fcntl.LOCK_EX)\n"
            "os.dup2(files[0].fileno(),9)\n"
            "os.environ['SDGE_COVERAGE_LOCK_FD']='9'\n"
            "os.environ['SDGE_COVERAGE_MARKER_FD']=str(files[1].fileno())\n"
            f"_report_tokens_under_O('VALUE=1\\n', {child_code!r})\n")
        parent = subprocess.Popen([sys.executable, str(driver)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists())
            child = int(ready.read_text())
            parent.kill()
            parent.wait(timeout=5)
            for path in locks:
                with path.open() as held:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            if child:
                os.kill(child, signal.SIGKILL)

    def test_parent_only_death_leaves_child_holding_both_locks(self):
        # A real intermediate Python process dies; only its measured child survives.
        lock_paths = [self.fixture / "outer.lock", self.fixture / "marker.lock"]
        ready = self.fixture / "ready"
        self.script.write_text("import os,time\nfrom pathlib import Path\n"
                               f"Path({str(ready)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n")
        driver = self.fixture / "driver.py"
        driver.write_text("import sys,fcntl\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(Path(__file__).parent)!r})\nimport coverage_runner as r\n"
            f"root=Path({str(self.fixture)!r}); run=Path({str(self.run)!r})\n"
            f"files=[open(p,'w') for p in {list(map(str, lock_paths))!r}]\n"
            "for f in files: fcntl.flock(f,fcntl.LOCK_EX)\n"
            "r.Task('orphan',root/'analysis/example.py',root,run,root,r.snapshot(root,run),"
            "pass_fds=tuple(f.fileno() for f in files)).start().finish()\n")
        parent = subprocess.Popen([sys.executable, str(driver)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists())
            child = int(ready.read_text())
            parent.kill()
            parent.wait(timeout=5)
            for path in lock_paths:
                with path.open() as held:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            if child:
                os.kill(child, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
