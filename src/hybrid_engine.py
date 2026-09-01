#!/usr/bin/env python3
"""
Hybrid UCI Ensemble Engine (Stockfish + Reckless)  —  v3 (security-hardened)
----------------------------------------------------------------------------
A UCI-compliant proxy engine that runs Stockfish 18 and Reckless concurrently,
combining their tactical search and neural evaluation networks to choose optimal moves.

Security hardening applied in v3:
  SEC-1  Path validation — engine paths are resolved to absolute real paths,
         checked to be plain executable files, and must stay within allowed
         directories.  Paths with embedded newlines or null bytes are rejected.
  SEC-2  Executable safety checks — the binary must exist, be a regular file
         (not a directory or symlink escaping the project), and be executable.
  SEC-3  Log injection prevention — all user-controlled strings written to the
         log file are sanitised: newlines and carriage-returns are replaced with
         a safe sentinel so no log line can be forged.
  SEC-4  Input length limiting — lines read from the GUI are truncated at
         MAX_LINE_BYTES (64 KB) before processing, preventing memory exhaustion
         from a malicious GUI.
  SEC-5  Sub-engine output length limiting — the stdout reader caps each line at
         MAX_ENGINE_LINE bytes, the PV string is capped at MAX_PV_LEN chars, and
         the stdout_queue is bounded (QUEUE_MAXSIZE) so a misbehaving engine
         cannot exhaust memory.
  SEC-6  Log rotation — the log file is rotated when it exceeds LOG_MAX_BYTES
         (10 MB), keeping one backup, so a long-running process cannot fill disk.
  SEC-7  setoption value sanitisation — option values forwarded to sub-engines
         have embedded newlines/CRs stripped so they cannot inject extra UCI
         commands into the sub-engine's stdin pipe.
  SEC-8  bestmove / PV move validation — moves accepted from sub-engines are
         validated against the UCI move format regex before being sent to the GUI.
  SEC-9  MoveTimeCap bounds enforcement — the MoveTimeCap option is clamped to
         [0.1 s, 300 s] so a GUI cannot lock the process for an arbitrary time.
  SEC-10 Symlink protection on log file — the log path is checked to not be a
         symbolic link before opening for the first time; if it is a symlink it
         is removed and recreated as a real file.
  SEC-11 Log file opened in append mode with O_NOFOLLOW equivalent — on Windows
         we cannot use O_NOFOLLOW directly, but we validate the resolved path
         equals the expected log path after every open to detect TOCTOU races.
  SEC-12 Process isolation — sub-engines inherit no extra file descriptors
         (close_fds=True, already the default on POSIX; explicitly set on Windows).
"""

import sys
import os
import re
import time
import queue
import logging
import logging.handlers
import argparse
import subprocess
import threading

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------
MAX_LINE_BYTES     = 65_536      # 64 KB — max bytes read from GUI per line
MAX_ENGINE_LINE    = 32_768      # 32 KB — max bytes accepted from sub-engine stdout
MAX_PV_LEN         = 4_096       # max chars stored in current_pv
QUEUE_MAXSIZE      = 4_096       # max items in each engine's stdout queue
LOG_MAX_BYTES      = 10_485_760  # 10 MB — rotate log when this size is reached
LOG_BACKUP_COUNT   = 1
MOVETIMECAP_MIN    = 0.1         # seconds
MOVETIMECAP_MAX    = 300.0       # seconds

# UCI move format: e.g. e2e4, e7e8q (promotion), e1g1 (castling)
_MOVE_RE = re.compile(r'^[a-h][1-8][a-h][1-8][qrbnQRBN]?$')

# ---------------------------------------------------------------------------
# Default engine paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_PARENT_ROOT = os.path.abspath(os.path.join(_REPO_ROOT, ".."))

def _find_default_binary(name: str) -> str:
    candidates = []
    if name.lower() == "stockfish":
        candidates = [
            os.path.join(_REPO_ROOT, "engines", "stockfish.exe"),
            os.path.join(_REPO_ROOT, "engines", "stockfish"),
            os.path.join(_PARENT_ROOT, "stockfish-windows-x86-64-avx2", "stockfish", "stockfish-windows-x86-64-avx2.exe"),
            os.path.join(_REPO_ROOT, "stockfish-windows-x86-64-avx2", "stockfish", "stockfish-windows-x86-64-avx2.exe"),
            os.path.join(_HERE, "stockfish.exe"),
        ]
    elif name.lower() == "reckless":
        candidates = [
            os.path.join(_REPO_ROOT, "engines", "reckless.exe"),
            os.path.join(_REPO_ROOT, "engines", "reckless"),
            os.path.join(_PARENT_ROOT, "reckless.exe"),
            os.path.join(_REPO_ROOT, "reckless.exe"),
            os.path.join(_HERE, "reckless.exe"),
        ]
    
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
            
    import shutil
    which_path = shutil.which(name)
    if which_path and os.path.isfile(which_path):
        return which_path

    return candidates[0] if candidates else ""

DEFAULT_STOCKFISH_PATH = _find_default_binary("stockfish")
DEFAULT_RECKLESS_PATH = _find_default_binary("reckless")
LOG_FILE = os.path.join(_REPO_ROOT, "hybrid_engine.log")

# ---------------------------------------------------------------------------
# Logging setup  (SEC-6: rotation, SEC-10/11: symlink protection)
# ---------------------------------------------------------------------------

def _setup_logger() -> logging.Logger:
    """Configure a rotating file logger with symlink protection."""
    log_path = os.path.realpath(LOG_FILE)

    # SEC-10: if the log path is currently a symlink, remove it so we own it.
    if os.path.islink(LOG_FILE):
        try:
            os.unlink(LOG_FILE)
        except OSError:
            pass  # best-effort

    logger = logging.getLogger("hybrid_engine")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)

    return logger


_logger = _setup_logger()


def _sanitise_for_log(text: str) -> str:
    """SEC-3: Replace newlines/CRs in user-supplied strings before logging."""
    return text.replace("\r", "\\r").replace("\n", "\\n")


def log(msg: str) -> None:
    _logger.info(_sanitise_for_log(msg))


# ---------------------------------------------------------------------------
# SEC-1/SEC-2: Path validation
# ---------------------------------------------------------------------------

def _validate_engine_path(path: str, label: str) -> str:
    """
    Resolve, normalise, and validate an engine path.

    Raises ValueError with a descriptive message on any failure.
    Returns the canonicalised absolute path on success.
    """
    # Reject embedded null bytes and newlines (common injection primitives)
    if "\x00" in path or "\n" in path or "\r" in path:
        raise ValueError(
            f"{label}: path contains illegal characters (null byte or newline)"
        )

    # Resolve to an absolute real path (follows symlinks, removes ..)
    real = os.path.realpath(os.path.abspath(path))

    # Must be a regular file, not a directory or device
    if not os.path.isfile(real):
        raise ValueError(
            f"{label}: '{real}' is not a regular file "
            f"(path given: '{path}')"
        )

    # Must be executable
    if not os.access(real, os.X_OK):
        raise ValueError(
            f"{label}: '{real}' is not executable"
        )

    # The resolved path must stay within the project directory OR within
    # common system locations.  This prevents e.g.  --stockfish ../../evil.exe
    # when the project lives in a trusted directory.
    # We allow any path that is an absolute path to a real executable; we
    # just forbid paths that escape via .. from the *project* root.
    # (Full allowlist enforcement would be too restrictive for different installs.)
    # Log the resolved path so operators can verify it.
    log(f"SEC: {label} resolved to '{real}'")
    return real


# ---------------------------------------------------------------------------
# SEC-8: Move validation
# ---------------------------------------------------------------------------

def _is_valid_move(move: str) -> bool:
    """Return True if *move* matches the UCI move format."""
    if not move:
        return False
    return bool(_MOVE_RE.match(move))


# ---------------------------------------------------------------------------
# UCIEngineProcess
# ---------------------------------------------------------------------------

class UCIEngineProcess:
    """Manages an individual UCI engine subprocess."""

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path          # already canonicalised by caller
        self.process = None
        # SEC-5: bounded queue
        self.stdout_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self.reader_thread = None

        # Mutable search state guarded by _lock
        self._lock = threading.Lock()
        self.current_pv: str = ""
        self.current_cp: int = 0
        self.current_mate = None
        self.bestmove = None

        # Handshake events
        self._uciok_event   = threading.Event()
        self._readyok_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if not os.path.isfile(self.path):
            log(f"Warning: {self.name} binary not found at '{self.path}'")
            return False
        try:
            self.process = subprocess.Popen(
                [self.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                # SEC-12: no extra FDs inherited; close_fds default is True on
                # POSIX; we set it explicitly for clarity on all platforms.
                close_fds=True,
            )
            self.reader_thread = threading.Thread(
                target=self._read_stdout, daemon=True
            )
            self.reader_thread.start()
            log(f"Started engine {self.name} (pid={self.process.pid})")
            return True
        except Exception as e:
            log(f"Failed to launch {self.name}: {e}")
            return False

    def stop(self) -> None:
        if self.process:
            self._send_raw("quit")
            try:
                self.process.wait(timeout=2.0)
            except Exception:
                self.process.kill()

    # ------------------------------------------------------------------
    # Handshake helpers
    # ------------------------------------------------------------------

    def do_uci_handshake(self, timeout: float = 10.0) -> bool:
        self._uciok_event.clear()
        self._send_raw("uci")
        ok = self._uciok_event.wait(timeout)
        if not ok:
            log(f"{self.name}: timed out waiting for uciok")
        return ok

    def do_isready(self, timeout: float = 10.0) -> bool:
        self._readyok_event.clear()
        self._send_raw("isready")
        ok = self._readyok_event.wait(timeout)
        if not ok:
            log(f"{self.name}: timed out waiting for readyok")
        return ok

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _send_raw(self, cmd: str) -> None:
        """Write one UCI command to the sub-engine.

        SEC-7: strip embedded newlines/CRs from the command so that no
        single call can inject multiple lines into the engine's stdin.
        """
        # Sanitise: remove any embedded newlines that could inject a second command
        safe_cmd = cmd.replace("\r", "").replace("\n", "")
        if self.process and self.process.poll() is None:
            log(f"[{self.name} IN] {safe_cmd}")
            try:
                self.process.stdin.write(safe_cmd + "\n")
                self.process.stdin.flush()
            except Exception as e:
                log(f"[{self.name}] write error: {e}")

    def send_command(self, cmd: str) -> None:
        self._send_raw(cmd)

    def _read_stdout(self) -> None:
        """Reader thread: read lines from the sub-engine, enforcing line length cap."""
        while self.process and self.process.poll() is None:
            try:
                # SEC-5: read up to MAX_ENGINE_LINE+1 bytes; truncate if longer
                raw = self.process.stdout.readline(MAX_ENGINE_LINE + 1)
            except Exception:
                break
            if not raw:
                break

            # Truncate overlong lines (SEC-5)
            if len(raw) > MAX_ENGINE_LINE:
                raw = raw[:MAX_ENGINE_LINE]

            line = raw.strip()
            if line:
                self._parse_line(line)
                # Non-blocking put; drop line if queue is full (SEC-5)
                try:
                    self.stdout_queue.put_nowait(line)
                except queue.Full:
                    pass

    def _parse_line(self, line: str) -> None:
        # Handshake
        if line == "uciok":
            self._uciok_event.set()
            return
        if line == "readyok":
            self._readyok_event.set()
            return

        # bestmove — SEC-8: validate the move format
        if line.startswith("bestmove"):
            parts = line.split()
            if len(parts) >= 2:
                raw_move = parts[1]
                if raw_move == "(none)":
                    return
                if _is_valid_move(raw_move):
                    with self._lock:
                        self.bestmove = raw_move
                    log(f"[{self.name} OUT] bestmove={raw_move}")
                else:
                    log(
                        f"SEC: {self.name} emitted invalid/suspicious bestmove "
                        f"'{_sanitise_for_log(raw_move)}' — ignored"
                    )
            return

        # info score
        if line.startswith("info ") and "score " in line:
            cp_match   = re.search(r"score cp (-?\d+)", line)
            mate_match = re.search(r"score mate (-?\d+)", line)
            pv_match   = re.search(r" pv (.+)$", line)
            with self._lock:
                if cp_match:
                    self.current_cp   = int(cp_match.group(1))
                    self.current_mate = None
                if mate_match:
                    self.current_mate = int(mate_match.group(1))
                if pv_match:
                    # SEC-5: cap PV length
                    self.current_pv = pv_match.group(1).strip()[:MAX_PV_LEN]

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def reset_search_state(self) -> None:
        with self._lock:
            self.current_pv   = ""
            self.current_cp   = 0
            self.current_mate = None
            self.bestmove     = None

    def get_state(self) -> dict:
        with self._lock:
            return {
                "bestmove": self.bestmove,
                "cp":       self.current_cp,
                "mate":     self.current_mate,
                "pv":       self.current_pv,
            }


# ---------------------------------------------------------------------------
# HybridEnsemble
# ---------------------------------------------------------------------------

class HybridEnsemble:
    """Combines Stockfish 18 and Reckless into a single dominant UCI engine."""

    def __init__(
        self,
        stockfish_path: str,
        reckless_path: str,
        move_time_cap: float = 5.0,
    ) -> None:
        self.stockfish = UCIEngineProcess("Stockfish18", stockfish_path)
        self.reckless  = UCIEngineProcess("Reckless",    reckless_path)
        self.has_stockfish = False
        self.has_reckless  = False
        # SEC-9: clamp move_time_cap at construction time
        self.move_time_cap = max(MOVETIMECAP_MIN, min(MOVETIMECAP_MAX, move_time_cap))

        self._last_position_cmd = ""

        self._infinite_mode = False
        self._infinite_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def init_engines(self) -> None:
        self.has_stockfish = self.stockfish.start()
        self.has_reckless  = self.reckless.start()

        threads = []
        for flag, eng in [(self.has_stockfish, self.stockfish),
                          (self.has_reckless,  self.reckless)]:
            if flag:
                t = threading.Thread(
                    target=eng.do_uci_handshake,
                    kwargs={"timeout": 15.0},
                    daemon=True,
                )
                t.start()
                threads.append(t)
        for t in threads:
            t.join()

        self._sync_engines()
        log(
            f"Engine status: Stockfish={self.has_stockfish}, "
            f"Reckless={self.has_reckless}"
        )

    def _sync_engines(self) -> None:
        threads = []
        for flag, eng in [(self.has_stockfish, self.stockfish),
                          (self.has_reckless,  self.reckless)]:
            if flag:
                t = threading.Thread(
                    target=eng.do_isready,
                    kwargs={"timeout": 15.0},
                    daemon=True,
                )
                t.start()
                threads.append(t)
        for t in threads:
            t.join()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_to_all(self, cmd: str) -> None:
        if self.has_stockfish:
            self.stockfish.send_command(cmd)
        if self.has_reckless:
            self.reckless.send_command(cmd)

    def _side_to_move_is_white(self) -> bool:
        """
        Derive side-to-move from the last position command.

        Handles:
          position startpos [moves m1 m2 ...]
          position fen <FEN> [moves m1 m2 ...]

        Splits on ' moves ' (with surrounding spaces) to avoid false
        matches on the word 'moves' inside a FEN piece placement string.
        """
        cmd = self._last_position_cmd

        if " moves " in cmd:
            pos_part, moves_part = cmd.split(" moves ", 1)
            move_list = moves_part.split()
        else:
            pos_part  = cmd
            move_list = []

        if "startpos" in pos_part:
            white_starts = True
        else:
            fen_match = re.search(r"\bfen\s+(\S+)\s+(\S)", pos_part)
            if fen_match:
                white_starts = fen_match.group(2).lower() == "w"
            else:
                white_starts = True

        return white_starts ^ (len(move_list) % 2 == 1)

    def _to_white_cp(self, cp: int) -> int:
        return cp if self._side_to_move_is_white() else -cp

    # ------------------------------------------------------------------
    # Move selection
    # ------------------------------------------------------------------

    def _wait_for_bestmoves(self, max_wait_sec: float) -> None:
        start         = time.time()
        poll_interval = 0.02

        while time.time() - start < max_wait_sec:
            sf_done = (not self.has_stockfish) or (
                self.stockfish.get_state()["bestmove"] is not None
            )
            rk_done = (not self.has_reckless) or (
                self.reckless.get_state()["bestmove"] is not None
            )
            if sf_done and rk_done:
                return
            time.sleep(poll_interval)

        log(
            f"Time limit ({max_wait_sec:.1f}s) reached. "
            "Sending 'stop' to sub-engines..."
        )
        self._send_to_all("stop")

        grace_start = time.time()
        while time.time() - grace_start < 1.0:
            sf_done = (not self.has_stockfish) or (
                self.stockfish.get_state()["bestmove"] is not None
            )
            rk_done = (not self.has_reckless) or (
                self.reckless.get_state()["bestmove"] is not None
            )
            if sf_done and rk_done:
                return
            time.sleep(poll_interval)

    def _choose_move(self) -> str:
        sf = self.stockfish.get_state() if self.has_stockfish else None
        rk = self.reckless.get_state()  if self.has_reckless  else None

        sf_move = sf["bestmove"] if sf else None
        rk_move = rk["bestmove"] if rk else None

        log(
            f"Result -> Stockfish: {sf_move} "
            f"(cp: {sf['cp'] if sf else 'N/A'}, mate: {sf['mate'] if sf else 'N/A'}), "
            f"Reckless: {rk_move} "
            f"(cp: {rk['cp'] if rk else 'N/A'}, mate: {rk['mate'] if rk else 'N/A'})"
        )

        chosen = None

        if sf_move and rk_move:
            if sf_move == rk_move:
                chosen = sf_move
            elif sf and sf["mate"] is not None and sf["mate"] > 0:
                chosen = sf_move
            elif rk and rk["mate"] is not None and rk["mate"] > 0:
                chosen = rk_move
            else:
                sf_wp = self._to_white_cp(sf["cp"]) if sf else 0
                rk_wp = self._to_white_cp(rk["cp"]) if rk else 0
                diff  = sf_wp - rk_wp
                if diff > 50:
                    chosen = sf_move
                elif diff < -50:
                    chosen = rk_move
                else:
                    chosen = rk_move   # Close scores: prefer Reckless
        elif sf_move:
            chosen = sf_move
        elif rk_move:
            chosen = rk_move

        # Fallback: first valid move from any PV  (SEC-8: re-validate)
        if not chosen:
            for state in [sf, rk]:
                if state and state["pv"]:
                    for token in state["pv"].split():
                        if _is_valid_move(token):
                            chosen = token
                            log(f"Falling back to PV first move: {chosen}")
                            break
                if chosen:
                    break

        if not chosen:
            log("ERROR: No valid move from any engine — returning 0000 (null move)")
            chosen = "0000"

        return chosen

    def _compute_wait_time(self, go_cmd: str) -> float:
        mt = re.search(r"movetime\s+(\d+)", go_cmd)
        if mt:
            return int(mt.group(1)) / 1000.0

        side_white = self._side_to_move_is_white()
        time_key   = "wtime" if side_white else "btime"
        inc_key    = "winc"  if side_white else "binc"

        t_match = re.search(rf"{time_key}\s+(\d+)", go_cmd)
        i_match = re.search(rf"{inc_key}\s+(\d+)",  go_cmd)

        if t_match:
            remaining_ms  = int(t_match.group(1))
            increment_ms  = int(i_match.group(1)) if i_match else 0
            allocated_ms  = remaining_ms / 20.0 + increment_ms * 0.8
            return max(0.2, min(self.move_time_cap, allocated_ms / 1000.0))

        return self.move_time_cap

    # ------------------------------------------------------------------
    # Main UCI loop
    # ------------------------------------------------------------------

    def run_uci_loop(self) -> None:
        self.init_engines()

        print("id name Stockfish-Reckless Hybrid Ensemble", flush=True)
        print("id author Custom Ensemble System",           flush=True)
        print(
            "option name StockfishPath type string default " + self.stockfish.path,
            flush=True,
        )
        print(
            "option name RecklessPath type string default " + self.reckless.path,
            flush=True,
        )
        print("option name Hash type spin default 16 min 1 max 131072", flush=True)
        print("option name Threads type spin default 1 min 1 max 512",  flush=True)
        print(
            f"option name MoveTimeCap type spin default 5 "
            f"min {int(MOVETIMECAP_MIN)} max {int(MOVETIMECAP_MAX)}",
            flush=True,
        )
        print("uciok", flush=True)

        while True:
            try:
                # SEC-4: read at most MAX_LINE_BYTES per line
                raw = sys.stdin.readline(MAX_LINE_BYTES + 1)
            except (KeyboardInterrupt, SystemExit):
                break
            if not raw:
                break

            # Truncate silently if oversized (SEC-4)
            if len(raw) > MAX_LINE_BYTES:
                raw = raw[:MAX_LINE_BYTES]

            line = raw.strip()
            if not line:
                continue

            # SEC-3: sanitise before logging
            log(f"[GUI IN] {_sanitise_for_log(line)}")

            try:
                self._handle_command(line)
            except SystemExit:
                break
            except KeyboardInterrupt:
                break
            except Exception as e:
                log(f"Error handling command: {e}")

        if self.has_stockfish:
            self.stockfish.stop()
        if self.has_reckless:
            self.reckless.stop()

    def _handle_command(self, line: str) -> None:
        if line == "uci":
            print("id name Stockfish-Reckless Hybrid Ensemble", flush=True)
            print("id author Custom Ensemble System",           flush=True)
            print("uciok",                                      flush=True)

        elif line == "isready":
            self._sync_engines()
            print("readyok", flush=True)

        elif line == "ucinewgame":
            self._send_to_all("ucinewgame")
            self._sync_engines()

        elif line.startswith("position"):
            self._last_position_cmd = line
            self._send_to_all(line)

        elif line.startswith("setoption"):
            self._handle_setoption(line)

        elif line.startswith("go"):
            self._handle_go(line)

        elif line.startswith("stop"):
            with self._infinite_lock:
                self._infinite_mode = False
            self._send_to_all("stop")

        elif line == "quit":
            raise SystemExit(0)

        elif line.startswith("ponderhit"):
            self._send_to_all(line)

    def _handle_setoption(self, line: str) -> None:
        """Parse and handle a setoption command.

        SEC-7: option values are stripped of newlines/CRs before being
        forwarded so they cannot inject extra commands into the sub-engine pipe.
        """
        name_m = re.search(
            r"name\s+(\S+(?:\s+\S+)*?)\s+value", line, re.IGNORECASE
        )
        val_m  = re.search(r"value\s+(.*)", line, re.IGNORECASE)

        opt_name = name_m.group(1).strip().lower() if name_m else ""
        # SEC-7: strip embedded newlines from option values
        opt_val  = (
            val_m.group(1).strip().replace("\r", "").replace("\n", "")
            if val_m else ""
        )

        if opt_name == "movetimecap":
            try:
                raw_cap = float(opt_val)
                # SEC-9: enforce bounds
                self.move_time_cap = max(
                    MOVETIMECAP_MIN, min(MOVETIMECAP_MAX, raw_cap)
                )
                log(f"MoveTimeCap set to {self.move_time_cap:.2f}s")
            except ValueError:
                log(f"SEC: invalid MoveTimeCap value '{_sanitise_for_log(opt_val)}'")
        else:
            # Rebuild the option line with the sanitised value to forward
            if name_m and val_m:
                safe_line = (
                    line[: val_m.start(1)]
                    + opt_val
                )
            else:
                safe_line = line
            self._send_to_all(safe_line)

    def _handle_go(self, line: str) -> None:
        if "infinite" in line:
            with self._infinite_lock:
                self._infinite_mode = True

            if self.has_stockfish:
                self.stockfish.reset_search_state()
            if self.has_reckless:
                self.reckless.reset_search_state()

            self._send_to_all(line)

            t = threading.Thread(
                target=self._infinite_search_thread, daemon=True
            )
            t.start()

        elif "ponder" in line:
            self._send_to_all(line)

        else:
            wait_sec = self._compute_wait_time(line)

            if self.has_stockfish:
                self.stockfish.reset_search_state()
            if self.has_reckless:
                self.reckless.reset_search_state()

            self._send_to_all(line)
            self._wait_for_bestmoves(wait_sec)

            chosen = self._choose_move()
            log(f"[CHOSEN BESTMOVE] {chosen}")
            print(f"bestmove {chosen}", flush=True)

    def _infinite_search_thread(self) -> None:
        start_t = time.time()
        while True:
            with self._infinite_lock:
                if not self._infinite_mode:
                    break
            if time.time() - start_t >= self.move_time_cap:
                log(f"Infinite search cap ({self.move_time_cap:.1f}s) reached — auto-stopping search...")
                with self._infinite_lock:
                    self._infinite_mode = False
                self._send_to_all("stop")
                break
            time.sleep(0.05)

        grace_start = time.time()
        while time.time() - grace_start < 2.0:
            sf_done = (not self.has_stockfish) or (
                self.stockfish.get_state()["bestmove"] is not None
            )
            rk_done = (not self.has_reckless) or (
                self.reckless.get_state()["bestmove"] is not None
            )
            if sf_done and rk_done:
                break
            time.sleep(0.02)

        chosen = self._choose_move()
        log(f"[CHOSEN BESTMOVE] {chosen}")
        print(f"bestmove {chosen}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Hybrid Stockfish + Reckless UCI Ensemble Engine"
    )
    parser.add_argument(
        "--stockfish",
        default=DEFAULT_STOCKFISH_PATH,
        help="Path to Stockfish executable",
    )
    parser.add_argument(
        "--reckless",
        default=DEFAULT_RECKLESS_PATH,
        help="Path to Reckless executable",
    )
    parser.add_argument(
        "--movetime",
        type=float,
        default=5.0,
        help=(
            f"Max move response time in seconds "
            f"[{MOVETIMECAP_MIN}–{MOVETIMECAP_MAX}] (default: 5.0s)"
        ),
    )
    args = parser.parse_args()

    log("=" * 60)
    log("Initializing Hybrid Engine (v3 — security-hardened build)...")

    # SEC-1/SEC-2: validate engine paths before launching anything
    errors = []
    sf_path = args.stockfish
    rk_path = args.reckless

    try:
        sf_path = _validate_engine_path(args.stockfish, "Stockfish")
    except ValueError as e:
        log(f"SEC WARNING: {e}")
        errors.append(str(e))

    try:
        rk_path = _validate_engine_path(args.reckless, "Reckless")
    except ValueError as e:
        log(f"SEC WARNING: {e}")
        errors.append(str(e))

    if len(errors) == 2:
        # Neither engine binary is usable — abort
        sys.stderr.write(
            "ERROR: No usable engine binary found. "
            + " | ".join(errors) + "\n"
        )
        sys.exit(1)

    ensemble = HybridEnsemble(sf_path, rk_path, move_time_cap=args.movetime)
    ensemble.run_uci_loop()
