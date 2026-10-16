"""checks: host runs, timeouts, the baseline comparison, failure signatures."""

import shutil
import subprocess
import time

import pytest

from operonx_project.checks import Check, CheckResult, compare, failure_signature, run_check, run_checks


async def test_a_passing_and_a_failing_check(tmp_path):
    ok, bad = await run_checks([Check("ok", "echo hello"), Check("bad", "echo oops >&2; exit 3")], tmp_path)
    assert ok.ok and ok.exit_code == 0 and "hello" in ok.tail
    assert not bad.ok and bad.exit_code == 3 and "oops" in bad.tail  # stderr is in the tail


async def test_the_tail_is_bounded(tmp_path):
    r = await run_check(Check("loud", "seq 1 5000"), tmp_path)
    lines = r.tail.splitlines()
    assert lines[-1] == "5000" and len(lines) <= 80


async def test_a_timeout_kills_the_whole_group(tmp_path):
    pidfile = tmp_path / "pid"
    t0 = time.monotonic()
    r = await run_check(Check("slow", f"sleep 300 & echo $! > {pidfile}; wait", timeout_s=1), tmp_path)
    assert r.timed_out and r.exit_code is None and not r.ok and time.monotonic() - t0 < 10
    pid = int(pidfile.read_text())
    for _ in range(100):
        if subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the check's background child survived its timeout")


async def test_pytest_failures_are_named(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_a():\n    assert 1\n\ndef test_b():\n    assert 0\n")
    r = await run_check(Check("pytest", "python -m pytest -q -p no:cacheprovider"), tmp_path)
    assert not r.ok and r.failures == ["test_x.py::test_b"]


def _r(name, ok, failures=(), code=None, tail=""):
    return CheckResult(name, ok, 0 if ok else (code or 1), 0.1, tail, failures=list(failures))


def test_compare_judges_only_new_failures():
    base = [_r("lint", True), _r("tests", False, ["t::a"]), _r("types", False, tail="x"), _r("old", False)]
    now = [_r("lint", False), _r("tests", False, ["t::a", "t::b"]), _r("types", True), _r("old", False)]
    c = compare(base, now)
    assert c.new == ["lint", "tests: t::b"] and c.old == ["old"] and c.fixed == ["types"] and not c.ok
    same = compare(base, [_r("lint", True), _r("tests", False, ["t::a"]), _r("types", False), _r("old", False)])
    assert same.ok and same.old == ["tests", "types", "old"]
    assert compare([], [_r("new", False)]).new == ["new"]  # no baseline: every failure is new


def test_failure_signature_is_stable_across_noise_and_tells_failures_apart():
    a = _r("tests", False, tail="Error at 0x7f3a in /home/u/w1/app.py line 12 after 3.2 s")
    b = _r("tests", False, tail="Error at 0x9c1b in /tmp/w2/app.py line 14 after 0.4 s")
    c = _r("tests", False, tail="KeyError: 'name'")
    assert failure_signature(a) == failure_signature(b) != failure_signature(c)
    ab, ba = _r("tests", False, ["t::a", "t::b"]), _r("tests", False, ["t::b", "t::a"])
    assert failure_signature(ab) == failure_signature(ba)
    assert failure_signature(_r("tests", True)) == ""


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker")
async def test_docker_checks_run_with_no_network(tmp_path):
    (tmp_path / "f.txt").write_text("inside\n")
    ok = await run_check(Check("cat", "cat f.txt", mode="docker", image="alpine:3.20", timeout_s=120), tmp_path)
    assert ok.ok and "inside" in ok.tail
    net = await run_check(
        Check("net", "wget -q -T 3 -O /dev/null http://example.com", mode="docker", image="alpine:3.20", timeout_s=120),
        tmp_path,
    )
    assert not net.ok


def test_docker_mode_needs_an_image():
    with pytest.raises(ValueError):
        Check("x", "true", mode="docker")
