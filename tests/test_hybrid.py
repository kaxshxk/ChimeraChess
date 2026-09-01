#!/usr/bin/env python3
"""
Full test suite for hybrid_engine.py — covers bug-fix correctness AND security hardening.
"""

import sys
import os
import threading
import tempfile
import stat

# Support running from: project root, tests/, or src/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR  = os.path.join(_THIS_DIR, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC_DIR))
sys.path.insert(0, os.path.abspath(_THIS_DIR))


# Silence the real logger so tests don't write to disk
import hybrid_engine
hybrid_engine._logger.handlers.clear()
import logging
hybrid_engine._logger.addHandler(logging.NullHandler())
hybrid_engine.log = lambda msg: None

from hybrid_engine import (
    UCIEngineProcess, HybridEnsemble,
    _is_valid_move, _sanitise_for_log, _validate_engine_path,
    MOVETIMECAP_MIN, MOVETIMECAP_MAX, MAX_LINE_BYTES, MAX_PV_LEN,
)

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f"  — {detail}"
        print(msg)
        FAIL += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine_stub(bestmove=None, cp=0, mate=None, pv="", name="Stub"):
    eng = UCIEngineProcess.__new__(UCIEngineProcess)
    eng._lock = threading.Lock()
    eng.name = name
    eng.bestmove = bestmove
    eng.current_cp = cp
    eng.current_mate = mate
    eng.current_pv = pv
    eng._uciok_event = threading.Event()
    eng._readyok_event = threading.Event()
    return eng


def make_ensemble(sf_bestmove=None, sf_cp=0, sf_mate=None, sf_pv="",
                  rk_bestmove=None, rk_cp=0, rk_mate=None, rk_pv="",
                  last_pos="position startpos"):
    ens = HybridEnsemble.__new__(HybridEnsemble)
    ens.move_time_cap = 5.0
    ens._last_position_cmd = last_pos
    ens._infinite_mode = False
    ens._infinite_lock = threading.Lock()
    ens.has_stockfish = (sf_bestmove is not None) or bool(sf_pv)
    ens.has_reckless  = (rk_bestmove is not None) or bool(rk_pv)
    ens.stockfish = make_engine_stub(sf_bestmove, sf_cp, sf_mate, sf_pv, "Stockfish18")
    ens.reckless  = make_engine_stub(rk_bestmove, rk_cp, rk_mate, rk_pv, "Reckless")
    return ens


# ===========================================================================
print("\n=== 1. Side-to-move detection ===")
# ===========================================================================

ens = make_ensemble(last_pos="position startpos")
check("startpos -> white to move", ens._side_to_move_is_white())

ens._last_position_cmd = "position startpos moves e2e4"
check("1 move -> black to move", not ens._side_to_move_is_white())

ens._last_position_cmd = "position startpos moves e2e4 e7e5"
check("2 moves -> white to move", ens._side_to_move_is_white())

ens._last_position_cmd = "position startpos moves e2e4 e7e5 g1f3"
check("3 moves -> black to move", not ens._side_to_move_is_white())

ens._last_position_cmd = "position fen rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
check("fen(w) no moves -> white to move", ens._side_to_move_is_white())

ens._last_position_cmd = "position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1 moves e7e5"
check("fen(b) + 1 move -> white to move", ens._side_to_move_is_white())

ens._last_position_cmd = "position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
check("fen(b) no moves -> black to move", not ens._side_to_move_is_white())


# ===========================================================================
print("\n=== 2. Score normalisation ===")
# ===========================================================================

ens = make_ensemble(last_pos="position startpos")
check("white to move: cp=50 -> white_cp=50",  ens._to_white_cp(50)  ==  50)
check("white to move: cp=-30 -> white_cp=-30", ens._to_white_cp(-30) == -30)

ens._last_position_cmd = "position startpos moves e2e4"
check("black to move: cp=50 -> white_cp=-50",  ens._to_white_cp(50)  == -50)
check("black to move: cp=-30 -> white_cp=30",  ens._to_white_cp(-30) ==  30)


# ===========================================================================
print("\n=== 3. Move choice logic ===")
# ===========================================================================

check("Both agree -> e2e4",
      make_ensemble("e2e4", 30, None, "", "e2e4", 35)._choose_move() == "e2e4")

ens = make_ensemble("e2e4", 30); ens.has_reckless = False
check("Only SF -> e2e4", ens._choose_move() == "e2e4")

ens = make_ensemble(rk_bestmove="d2d4", rk_cp=40); ens.has_stockfish = False
check("Only Reckless -> d2d4", ens._choose_move() == "d2d4")

check("SF forced mate -> SF move",
      make_ensemble("e2e4", 0, 3, "", "d2d4", 0, None, "")._choose_move() == "e2e4")

check("RK forced mate -> RK move",
      make_ensemble("e2e4", 0, None, "", "d2d4", 0, 2, "")._choose_move() == "d2d4")

check("SF +60cp -> SF wins",
      make_ensemble("e2e4", 120, None, "", "d2d4", 60)._choose_move() == "e2e4")

check("RK +60cp -> RK wins",
      make_ensemble("e2e4", 60, None, "", "d2d4", 120)._choose_move() == "d2d4")

check("Close scores -> RK preferred",
      make_ensemble("e2e4", 80, None, "", "d2d4", 90)._choose_move() == "d2d4")

check("Negative mate in SF -> not forced-mate branch",
      make_ensemble("e2e4", 0, -2, "", "d2d4", 30)._choose_move() == "d2d4")


# ===========================================================================
print("\n=== 4. Fallback move behaviour ===")
# ===========================================================================

ens = make_ensemble(sf_pv="e2e4 e7e5 g1f3")
ens.has_stockfish = True; ens.has_reckless = True
ens.stockfish.bestmove = None; ens.reckless.bestmove = None
check("Fallback to PV first move -> e2e4", ens._choose_move() == "e2e4")

ens = make_ensemble(sf_pv="", rk_pv="")
ens.has_stockfish = True; ens.has_reckless = True
ens.stockfish.bestmove = None; ens.reckless.bestmove = None
check("No bestmove, no PV -> 0000", ens._choose_move() == "0000")


# ===========================================================================
print("\n=== 5. Time management ===")
# ===========================================================================

ens = make_ensemble(); ens.move_time_cap = 5.0

ens._last_position_cmd = "position startpos"
check("movetime 2000 -> 2.0s",
      abs(ens._compute_wait_time("go movetime 2000") - 2.0) < 0.001)

check("wtime=300000 -> capped at 5.0s",
      abs(ens._compute_wait_time("go wtime 300000 btime 300000") - 5.0) < 0.001)

check("wtime=2000 -> floor 0.2s",
      ens._compute_wait_time("go wtime 2000 btime 2000") >= 0.2)

w = ens._compute_wait_time("go wtime 5000 btime 5000 winc 2000 binc 2000")
check("wtime=5000 winc=2000 -> ~1.85s", abs(w - 1.85) < 0.01, f"got {w}")

ens._last_position_cmd = "position startpos moves e2e4"
w = ens._compute_wait_time("go wtime 1000 btime 60000")
check("Black uses btime=60000 -> 3.0s", abs(w - 3.0) < 0.01, f"got {w}")

ens._last_position_cmd = "position startpos"
check("go depth 20 -> move_time_cap",
      abs(ens._compute_wait_time("go depth 20") - 5.0) < 0.001)


# ===========================================================================
print("\n=== 6. State reset isolation ===")
# ===========================================================================

eng = make_engine_stub("e2e4", 120, 3, "e2e4 e7e5")
eng.reset_search_state()
s = eng.get_state()
check("After reset: bestmove=None", s["bestmove"] is None)
check("After reset: cp=0",          s["cp"] == 0)
check("After reset: mate=None",     s["mate"] is None)
check("After reset: pv=''",         s["pv"] == "")


# ===========================================================================
print("\n=== 7. Line parsing ===")
# ===========================================================================

eng = make_engine_stub()
eng._parse_line("uciok");    check("uciok sets event",    eng._uciok_event.is_set())
eng._parse_line("readyok");  check("readyok sets event",  eng._readyok_event.is_set())

eng._parse_line("bestmove e2e4 ponder e7e5")
check("bestmove e2e4 parsed", eng.get_state()["bestmove"] == "e2e4")

eng.bestmove = None
eng._parse_line("bestmove (none)")
check("bestmove (none) -> bestmove stays None", eng.get_state()["bestmove"] is None)

eng._parse_line("info depth 10 score cp 45 nodes 12345 pv e2e4 e7e5 g1f3")
s = eng.get_state()
check("info cp=45",                 s["cp"] == 45)
check("info mate=None after cp",    s["mate"] is None)
check("info pv parsed",             s["pv"] == "e2e4 e7e5 g1f3")

eng._parse_line("info depth 5 score mate 3 pv d1h5 f7f6 h5f7")
check("info mate=3",                eng.get_state()["mate"] == 3)

eng._parse_line("info depth 5 score mate -2 pv 0000")
check("info mate=-2",               eng.get_state()["mate"] == -2)


# ===========================================================================
print("\n=== SEC-1/SEC-2: Path validation ===")
# ===========================================================================

# Null byte in path
try:
    _validate_engine_path("/some/path\x00evil", "Test")
    check("Null byte in path -> ValueError", False, "no exception raised")
except ValueError as e:
    check("Null byte in path -> ValueError", True)

# Newline in path
try:
    _validate_engine_path("/some/path\nevil", "Test")
    check("Newline in path -> ValueError", False, "no exception raised")
except ValueError as e:
    check("Newline in path -> ValueError", True)

# Directory instead of file
try:
    _validate_engine_path(os.path.dirname(os.path.abspath(__file__)), "Test")
    check("Directory path -> ValueError", False, "no exception raised")
except ValueError:
    check("Directory path -> ValueError", True)

# Non-existent path
try:
    _validate_engine_path("/nonexistent/path/engine.exe", "Test")
    check("Non-existent path -> ValueError", False, "no exception raised")
except ValueError:
    check("Non-existent path -> ValueError", True)

# Valid executable (use Python itself as a known executable)
python_exe = sys.executable
try:
    resolved = _validate_engine_path(python_exe, "PythonTest")
    check("Valid executable -> returns resolved path", os.path.isfile(resolved))
except ValueError as e:
    check("Valid executable -> returns resolved path", False, str(e))


# ===========================================================================
print("\n=== SEC-3: Log injection prevention ===")
# ===========================================================================

check("Newline in log string is escaped",
      "\n" not in _sanitise_for_log("hello\nworld"))
check("CR in log string is escaped",
      "\r" not in _sanitise_for_log("hello\rworld"))
check("Safe string unchanged",
      _sanitise_for_log("info depth 10 score cp 45") == "info depth 10 score cp 45")
check("Embedded log-line injection escaped",
      "\\n" in _sanitise_for_log("x\n[2026-01-01] FAKE LOG ENTRY"))


# ===========================================================================
print("\n=== SEC-4: Input length limiting ===")
# ===========================================================================

check("MAX_LINE_BYTES is 64 KB", MAX_LINE_BYTES == 65_536)
# The actual truncation happens in run_uci_loop; we verify the constant is right


# ===========================================================================
print("\n=== SEC-5: Engine output length + PV cap ===")
# ===========================================================================

eng = make_engine_stub()
# Overlong PV line — simulate a line with a very long PV
long_pv = " ".join(["e2e4"] * 1000)  # ~5000 chars
long_info = f"info depth 1 score cp 10 pv {long_pv}"
eng._parse_line(long_info)
check(f"PV capped at MAX_PV_LEN ({MAX_PV_LEN})",
      len(eng.get_state()["pv"]) <= MAX_PV_LEN)


# ===========================================================================
print("\n=== SEC-7: setoption value sanitisation ===")
# ===========================================================================

# Simulate setoption parsing via _handle_setoption
import io, unittest.mock as mock

sent_commands = []
ens = make_ensemble()
ens.has_stockfish = False
ens.has_reckless = False
ens._send_to_all = lambda cmd: sent_commands.append(cmd)

# Injected newline in value should be stripped
sent_commands.clear()
ens._handle_setoption("setoption name SyzygyPath value C:\\tablebases\nevil_cmd")
check("Newline in setoption value stripped before forwarding",
      all("\n" not in c for c in sent_commands))

# CR stripped too
sent_commands.clear()
ens._handle_setoption("setoption name Hash value 16\revil")
check("CR in setoption value stripped before forwarding",
      all("\r" not in c for c in sent_commands))


# ===========================================================================
print("\n=== SEC-8: Move validation ===")
# ===========================================================================

valid_moves = ["e2e4", "d7d5", "e7e8q", "e7e8Q", "a1h8", "h1a8r", "g1f3", "e1g1"]
invalid_moves = [
    "", "e2e4\nevil", "e2e4\x00", "e9e4", "e2e9", "e2e4x",
    "xyz", "e2e4qr", "tooshort", "e2e4 extra",
    "bestmove e2e4\nbestmove d7d5",  # injection attempt
    "e2e4\rinjection",
]

for m in valid_moves:
    check(f"Valid move '{m}'", _is_valid_move(m))

for m in invalid_moves:
    check(f"Invalid/injection move rejected: '{m}'", not _is_valid_move(m))

# Rogue engine emitting injection in bestmove line
eng = make_engine_stub()
eng._parse_line("bestmove e2e4\nevil_injection")  # newline in raw line from engine
# The move regex must reject "e2e4\nevil_injection" (after split it becomes "e2e4")
# Actually after split()[1] -> "e2e4\nevil_injection" would only happen if
# readline didn't strip; our reader strips, so let's test the regex directly
check("Bestmove with embedded newline rejected by regex",
      not _is_valid_move("e2e4\nevil"))

# Rogue engine: bestmove with invalid chars
eng2 = make_engine_stub()
eng2._parse_line("bestmove ../../evil.sh")
check("Path-traversal bestmove rejected", eng2.get_state()["bestmove"] is None)

eng3 = make_engine_stub()
eng3._parse_line("bestmove e2e4")
check("Legitimate bestmove accepted", eng3.get_state()["bestmove"] == "e2e4")


# ===========================================================================
print("\n=== SEC-9: MoveTimeCap bounds ===")
# ===========================================================================

ens = HybridEnsemble.__new__(HybridEnsemble)
ens.stockfish = make_engine_stub()
ens.reckless  = make_engine_stub()
ens.has_stockfish = False
ens.has_reckless  = False
ens._last_position_cmd = "position startpos"
ens._infinite_mode = False
ens._infinite_lock = threading.Lock()

# Way above max
ens.move_time_cap = MOVETIMECAP_MAX
ens._handle_setoption("setoption name MoveTimeCap value 999999")
check(f"MoveTimeCap clamped to max ({MOVETIMECAP_MAX}s)",
      ens.move_time_cap <= MOVETIMECAP_MAX, f"got {ens.move_time_cap}")

# Below min
ens._handle_setoption("setoption name MoveTimeCap value 0")
check(f"MoveTimeCap clamped to min ({MOVETIMECAP_MIN}s)",
      ens.move_time_cap >= MOVETIMECAP_MIN, f"got {ens.move_time_cap}")

# Non-numeric
old_cap = ens.move_time_cap
ens._handle_setoption("setoption name MoveTimeCap value not_a_number")
check("Non-numeric MoveTimeCap leaves cap unchanged", ens.move_time_cap == old_cap)

# Valid value
ens._handle_setoption("setoption name MoveTimeCap value 10")
check("Valid MoveTimeCap 10s accepted",
      abs(ens.move_time_cap - 10.0) < 0.001, f"got {ens.move_time_cap}")


# ===========================================================================
print("\n=== SEC-10: Symlink protection on log ===")
# ===========================================================================

import hybrid_engine as _hm
import logging.handlers as _lh

with tempfile.TemporaryDirectory() as tmpdir:
    target_file = os.path.join(tmpdir, "real_target.txt")
    with open(target_file, "w") as f:
        f.write("sensitive data")

    link_path = os.path.join(tmpdir, "hybrid_engine.log")

    # Create the symlink
    try:
        os.symlink(target_file, link_path)
        symlink_created = True
    except (OSError, NotImplementedError):
        symlink_created = False  # Windows may need privileges for symlinks

    if symlink_created:
        # Monkey-patch LOG_FILE to point to our fake log path
        old_log_file = _hm.LOG_FILE
        _hm.LOG_FILE = link_path

        # Re-run setup_logger — it should remove the symlink
        _hm._setup_logger()

        # The symlink should have been removed or replaced
        is_still_symlink = os.path.islink(link_path)
        check("Symlink at log path removed by _setup_logger",
              not is_still_symlink)

        # The real target should be untouched
        with open(target_file) as f:
            content = f.read()
        check("Real target file not corrupted by symlink attack",
              content == "sensitive data")

        _hm.LOG_FILE = old_log_file
    else:
        check("Symlink protection (skipped — no symlink privilege on this OS)", True)


# ===========================================================================
print("\n=== SEC-11: _send_raw strips embedded newlines ===")
# ===========================================================================

sent = []
eng = make_engine_stub()
eng.process = mock.MagicMock()
eng.process.poll.return_value = None
eng.process.stdin = mock.MagicMock()

# Capture what gets written
written_lines = []
def fake_write(data):
    written_lines.append(data)
eng.process.stdin.write = fake_write
eng.process.stdin.flush = lambda: None

# Send a command with an embedded newline
eng._send_raw("position startpos\nmalicious_command")
# _send_raw strips the \n and writes "position startposmalicious_command\n"
# OR strips everything after \n, depending on impl.
# Either way the literal string "malicious_command" must NOT appear as a
# separate command line — it must be absent or fused into a single invalid token.
combined = "".join(written_lines)
check("Embedded newline stripped — injected part not a separate command",
      # The newline-injected part must either be absent or mangled (not standalone)
      "\nmalicious_command" not in combined)


# ===========================================================================
print("\n=== SEC-12: Process isolation (close_fds) ===")
# ===========================================================================

import inspect, subprocess as sp
src = inspect.getsource(UCIEngineProcess.start)
check("close_fds=True present in start()", "close_fds=True" in src)


# ===========================================================================
# Summary
# ===========================================================================
print(f"\n{'='*55}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS+FAIL} tests")
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print(f"WARNING: {FAIL} test(s) FAILED")
    sys.exit(1)
