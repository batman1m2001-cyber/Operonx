import asyncio
import sys

sys.path.insert(0, "/home/thanglq/Operon")
from operonx.agents.heartbeat import Heartbeat  # noqa: E402

TICK = 0.05


class FakeSession:
    def __init__(self, *, delay=0.0, fail=False):
        self.sent = []
        self.delay = delay
        self.fail = fail

    async def send(self, text, *, on_approval=None):
        self.sent.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("nope")
        return {"final": {"content": "ok"}}


async def h1_max_beats_queue_overshoot():
    print("\n=== H1: max_beats with overlap='queue' ===")
    s = FakeSession(delay=TICK * 5)
    hb = Heartbeat(s, "x", interval=TICK, overlap="queue", max_beats=2)
    await hb.start()
    await asyncio.sleep(TICK * 40)
    print(f"  max_beats=2 -> beats={hb.beats} sends={len(s.sent)} running={hb.running}")
    await hb.stop()
    print("  VERDICT:", "OVERSHOOT" if len(s.sent) > 2 else "ok")


async def h2_stop_cancelled_keeps_beating():
    print("\n=== H2: stop() cancelled from outside ===")
    s = FakeSession(delay=TICK * 20)  # a beat far longer than stop()'s patience
    hb = Heartbeat(s, "x", interval=TICK)
    await hb.start()
    await asyncio.sleep(TICK * 2)
    # An outer deadline gives up on stop() -- e.g. a shutdown timeout.
    try:
        await asyncio.wait_for(hb.stop(timeout=30), timeout=TICK * 4)
        print("  wait_for(stop) RETURNED NORMALLY (CancelledError was swallowed)")
    except asyncio.TimeoutError:
        print("  wait_for(stop) raised TimeoutError (expected behaviour)")
    except Exception as e:
        print("  wait_for(stop) raised", type(e).__name__, e)
    print("  running =", hb.running, " (caller believes it is stopped)")
    n = len(s.sent)
    await asyncio.sleep(TICK * 30)
    print(f"  sends went {n} -> {len(s.sent)} after 'stopping'")
    print("  VERDICT:", "STILL BEATING, UNSTOPPABLE" if len(s.sent) > n else "ok")


async def h3_stop_then_start_double_loop():
    print("\n=== H3: stop() timing out, then start() again ===")
    s = FakeSession(delay=TICK * 30)
    hb = Heartbeat(s, "x", interval=TICK)
    await hb.start()
    await asyncio.sleep(TICK * 2)
    try:
        await hb.stop(timeout=TICK * 2)
    except asyncio.TimeoutError:
        print("  stop() timed out and cancelled, as documented")
    print("  running:", hb.running)


async def h4_beat_task_exception_unretrieved():
    print("\n=== H4: BaseException from on_error escapes the beat task ===")

    def bad(_e):
        raise KeyboardInterrupt("reporter exploded")

    s = FakeSession(fail=True)
    hb = Heartbeat(s, "x", interval=TICK, on_error=bad)
    await hb.start()
    await asyncio.sleep(TICK * 8)
    print("  beats:", hb.beats, "errors:", hb.errors, "running:", hb.running)
    try:
        await hb.stop(timeout=2)
    except Exception as e:
        print("  stop raised", type(e).__name__, e)


async def h5_running_after_max_beats():
    print("\n=== H5: running/stop after max_beats ===")
    s = FakeSession()
    hb = Heartbeat(s, "x", interval=TICK, max_beats=2)
    await hb.start()
    await asyncio.sleep(TICK * 10)
    print("  beats:", hb.beats, "running:", hb.running, "task done:", hb._task.done())
    print("  loop task exception:", hb._task.exception())
    # restart resets _started but not counters
    await hb.start()
    await asyncio.sleep(TICK * 10)
    print("  after restart beats:", hb.beats, "(counters not reset)")
    await hb.stop()


async def h6_pending_leak_across_chains():
    print("\n=== H6: _pending set for a chain that is exiting ===")
    s = FakeSession(delay=TICK * 3)
    hb = Heartbeat(s, "x", interval=TICK, overlap="queue")
    await hb.start()
    await asyncio.sleep(TICK * 30)
    gaps = []
    print("  sends:", len(s.sent), "beats:", hb.beats, "skipped:", hb.skipped)
    await hb.stop()


async def h7_jitter_hot_loop():
    print("\n=== H7: jitter=1.0 wait distribution ===")
    hb = Heartbeat(FakeSession(), "x", interval=1.0, jitter=1.0)
    waits = [hb._wait_for() for _ in range(20000)]
    tiny = sum(1 for w in waits if w < 0.01)
    print(f"  min={min(waits):.5f} mean={sum(waits)/len(waits):.3f} <10ms: {tiny}/20000")


async def h8_stop_never_started():
    print("\n=== H8: stop() before start(), and stop() concurrency ===")
    hb = Heartbeat(FakeSession(delay=TICK * 10), "x", interval=TICK)
    await hb.stop()
    print("  stop() before start: ok")
    await hb.start()
    await asyncio.sleep(TICK * 2)
    a = asyncio.create_task(hb.stop())
    await asyncio.sleep(0)
    b = asyncio.create_task(hb.stop())
    lo = asyncio.get_running_loop().time()
    await b
    print(f"  second concurrent stop() returned after {asyncio.get_running_loop().time()-lo:.3f}s")
    print("  (first stop still running: %s)" % (not a.done()))
    await a


async def main():
    for h in (
        h7_jitter_hot_loop,
        h1_max_beats_queue_overshoot,
        h5_running_after_max_beats,
        h6_pending_leak_across_chains,
        h4_beat_task_exception_unretrieved,
        h8_stop_never_started,
        h3_stop_then_start_double_loop,
        h2_stop_cancelled_keeps_beating,
    ):
        try:
            await asyncio.wait_for(h(), timeout=30)
        except Exception as e:
            print(f"  !! {h.__name__}: {type(e).__name__}: {e}")


asyncio.run(main())
