"""The failure-reporting half of every hand-rolled test runner in this repo.

WHAT THIS EXISTS TO STOP (issue #209). These suites run their cases through a
`main()` that catches the outcomes it expects -- `SkipCase`, `AssertionError` --
and nothing else. Any other exception escapes the loop and ends the RUN:

  * no FAIL line, so nothing names the case that broke;
  * no tally, so the count that would reveal the truncation is never printed;
  * and every case after it never executes.

The blast radius depends on where the bad case happens to sit in the list, which
is why it reads as "a few cases are missing" rather than as a defect. Measured
on test_household.py: a KeyError injected into case 2 of 5 ended the run with a
traceback, cases 3-5 unrun, and no "N/M passed" line anywhere in the output.

IT HAS BEEN FIXED LOCALLY TWICE, for two different exception classes -- SystemExit
in test_report_tokens (#178), KeyError in test_parse_bills (#208) -- by two people
who each treated it as a defect in one file. The third instance would have been a
third class. This module is the shape rather than the instance: a suite that
imports it is guarded against the classes nobody has thought of yet.

WHY `SystemExit` IS NAMED SEPARATELY. It inherits from BaseException, not
Exception, so `except Exception` walks straight past it. That is not hypothetical
here: analysis/household.py fails closed with SystemExit when the private archive
is absent, which is exactly the state CI runs in, and a case that provokes one
outside its own try would otherwise kill the run on CI and nowhere else. That
shipped a red CI once already (#221).

WHAT IS DELIBERATELY NOT CAUGHT. KeyboardInterrupt and GeneratorExit are the
other BaseExceptions, and neither is a case outcome. A runner that swallowed
Ctrl-C and carried on through the remaining cases would be a worse instrument
than one that dies.

THE RUNNER'S OWN EXIT IS NOT ROUTED THROUGH HERE. Every suite ends with
`sys.exit(main())`: main RETURNS the status and the SystemExit is raised outside
the case loop, so a deliberate exit(1) from the failure path stays an exit code
and is never re-reported as a case failure.
"""
import sys
import traceback

# The classes a case may fail with. AssertionError is listed first for the
# reader even though Exception subsumes it -- the point of the tuple is that the
# first two are the EXPECTED outcomes and the third is everything else.
CASE_FAILURES = (AssertionError, SystemExit, Exception)


def report_case_failure(case, exc):
    """One FAIL line for a case that did not pass, whatever it raised.

    An AssertionError carries its own diagnosis and prints exactly as it always
    did, so no existing output changes shape. Anything else gets its class name
    and its traceback -- on stdout, under its own FAIL line, because the runner
    keeps going and the frame is otherwise lost for good, and because stderr
    would interleave somewhere else in the log through a separate buffer.
    """
    kind = "" if isinstance(exc, AssertionError) else f"{type(exc).__name__}: "
    print(f"FAIL  {case.__name__}: {kind}{exc}")
    if not isinstance(exc, AssertionError):
        traceback.print_exc(file=sys.stdout)
