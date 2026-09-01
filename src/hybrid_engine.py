#!/usr/bin/env python3
"""
Hybrid UCI Ensemble Engine (Stockfish + Reckless)
------------------------------------------------
A UCI-compliant proxy engine that runs Stockfish 18 and Reckless concurrently,
combining their tactical search and neural evaluation networks to choose optimal moves.
"""

import sys
import os
import subprocess
import threading
import queue
import time
import re
import argparse

# Dynamic default paths
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.abspath(os.path.join(_HERE, ".."))

def _find_binary(name):
    candidates = [
        os.path.join(_HERE, "engines", f"{name}.exe"),
        os.path.join(_HERE, "ChimeraChess", "engines", f"{name}.exe"),
        os.path.join(_HERE, f"{name}.exe"),
        os.path.join(_PARENT, f"{name}.exe"),
        os.path.join(_PARENT, "ChimeraChess", "engines", f"{name}.exe"),
        os.path.join(_PARENT, "engines", f"{name}.exe"),
        os.path.join(_HERE, "stockfish-windows-x86-64-avx2", "stockfish", "stockfish-windows-x86-64-avx2.exe"),
        os.path.join(_PARENT, "stockfish-windows-x86-64-avx2", "stockfish", "stockfish-windows-x86-64-avx2.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(_HERE, f"{name}.exe"))

DEFAULT_STOCKFISH_PATH = _find_binary("stockfish")
DEFAULT_RECKLESS_PATH = _find_binary("reckless")

LOG_FILE = os.path.abspath(os.path.join(_HERE, "hybrid_engine.log"))

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

class UCIEngineProcess:
    """Manages an individual UCI engine process."""
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.process = None
        self.stdout_queue = queue.Queue()
        self.reader_thread = None
        self.current_pv = ""
        self.current_cp = 0
        self.current_mate = None
        self.bestmove = None

    def start(self):
        if not os.path.exists(self.path):
            log(f"Warning: {self.name} binary not found at '{self.path}'")
            return False
        try:
            self.process = subprocess.Popen(
                [self.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            self.reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
            self.reader_thread.start()
            self.send_command("uci")
            log(f"Started engine {self.name} ({self.path})")
            return True
        except Exception as e:
            log(f"Failed to launch {self.name}: {e}")
            return False

    def _read_stdout(self):
        while self.process and self.process.poll() is None:
            line = self.process.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line:
                self._parse_line(line)
                self.stdout_queue.put(line)

    def _parse_line(self, line):
        if line.startswith("bestmove"):
            parts = line.split()
            if len(parts) >= 2:
                self.bestmove = parts[1]
                log(f"[{self.name} OUT] bestmove={self.bestmove}")
        elif line.startswith("info ") and "score " in line:
            cp_match = re.search(r"score cp (-?\d+)", line)
            if cp_match:
                self.current_cp = int(cp_match.group(1))
                self.current_mate = None
            mate_match = re.search(r"score mate (-?\d+)", line)
            if mate_match:
                self.current_mate = int(mate_match.group(1))

            pv_match = re.search(r" pv (.+)$", line)
            if pv_match:
                self.current_pv = pv_match.group(1).strip()

    def send_command(self, cmd):
        if self.process and self.process.poll() is None:
            log(f"[{self.name} IN] {cmd}")
            self.process.stdin.write(cmd + "\n")
            self.process.stdin.flush()

    def stop(self):
        if self.process:
            self.send_command("quit")
            try:
                self.process.wait(timeout=1.0)
            except Exception:
                self.process.kill()

class HybridEnsemble:
    """Combines Stockfish 18 and Reckless into a single dominant UCI engine."""
    def __init__(self, stockfish_path, reckless_path, move_time_cap=3.0):
        self.stockfish = UCIEngineProcess("Stockfish18", stockfish_path)
        self.reckless = UCIEngineProcess("Reckless", reckless_path)
        self.has_reckless = False
        self.has_stockfish = False
        self.move_time_cap = move_time_cap

    def init_engines(self):
        self.has_stockfish = self.stockfish.start()
        self.has_reckless = self.reckless.start()
        log(f"Engine status: Stockfish={self.has_stockfish}, Reckless={self.has_reckless}")

    def send_to_all(self, cmd):
        if self.has_stockfish:
            self.stockfish.send_command(cmd)
        if self.has_reckless:
            self.reckless.send_command(cmd)

    def resolve_best_move(self, max_wait_sec=3.0):
        start_time = time.time()
        
        # Clear previous best moves
        if self.has_stockfish:
            self.stockfish.bestmove = None
        if self.has_reckless:
            self.reckless.bestmove = None

        # Wait for engines to finish search or emit bestmove
        while time.time() - start_time < max_wait_sec:
            sf_done = (not self.has_stockfish) or (self.stockfish.bestmove is not None)
            rk_done = (not self.has_reckless) or (self.reckless.bestmove is not None)
            if sf_done and rk_done:
                break
            time.sleep(0.05)

        # If sub-engines haven't emitted bestmove, send stop
        if (self.has_stockfish and self.stockfish.bestmove is None) or \
           (self.has_reckless and self.reckless.bestmove is None):
            log(f"Time limit ({max_wait_sec}s) reached. Sending 'stop' to sub-engines...")
            self.send_to_all("stop")
            
            stop_start = time.time()
            while time.time() - stop_start < 0.8:
                sf_done = (not self.has_stockfish) or (self.stockfish.bestmove is not None)
                rk_done = (not self.has_reckless) or (self.reckless.bestmove is not None)
                if sf_done and rk_done:
                    break
                time.sleep(0.05)

        sf_move = self.stockfish.bestmove if self.has_stockfish else None
        rk_move = self.reckless.bestmove if self.has_reckless else None

        log(f"Result -> Stockfish: {sf_move} (cp: {self.stockfish.current_cp}, mate: {self.stockfish.current_mate}), Reckless: {rk_move} (cp: {self.reckless.current_cp}, mate: {self.reckless.current_mate})")

        # Decision Logic
        chosen_move = None
        if sf_move and rk_move:
            if sf_move == rk_move:
                chosen_move = sf_move
            elif self.stockfish.current_mate is not None and self.stockfish.current_mate > 0:
                chosen_move = sf_move
            elif self.reckless.current_mate is not None and self.reckless.current_mate > 0:
                chosen_move = rk_move
            else:
                cp_diff = self.stockfish.current_cp - self.reckless.current_cp
                if cp_diff > 50:
                    chosen_move = sf_move
                elif cp_diff < -50:
                    chosen_move = rk_move
                else:
                    chosen_move = rk_move if rk_move else sf_move
        elif sf_move:
            chosen_move = sf_move
        elif rk_move:
            chosen_move = rk_move

        if not chosen_move:
            for eng in [self.stockfish, self.reckless]:
                if eng.current_pv:
                    pv_first = eng.current_pv.split()[0]
                    if len(pv_first) >= 4:
                        chosen_move = pv_first
                        break

        if not chosen_move:
            chosen_move = "e2e4"

        return chosen_move

    def run_uci_loop(self):
        self.init_engines()

        print("id name Stockfish-Reckless Hybrid Ensemble", flush=True)
        print("id author Custom Ensemble System", flush=True)
        print("option name StockfishPath type string default " + self.stockfish.path, flush=True)
        print("option name RecklessPath type string default " + self.reckless.path, flush=True)
        print("uciok", flush=True)

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                log(f"[GUI IN] {line}")

                if line == "uci":
                    print("id name Stockfish-Reckless Hybrid Ensemble", flush=True)
                    print("id author Custom Ensemble System", flush=True)
                    print("uciok", flush=True)
                elif line == "isready":
                    print("readyok", flush=True)
                elif line.startswith("position"):
                    self.send_to_all(line)
                elif line.startswith("ucinewgame"):
                    self.send_to_all(line)
                elif line.startswith("go"):
                    self.send_to_all(line)
                    
                    wait_sec = self.move_time_cap
                    movetime_match = re.search(r"movetime (\d+)", line)
                    if movetime_match:
                        wait_sec = (int(movetime_match.group(1)) / 1000.0)
                    elif "wtime" in line and "btime" in line:
                        wtime_m = re.search(r"wtime (\d+)", line)
                        btime_m = re.search(r"btime (\d+)", line)
                        wtime = int(wtime_m.group(1)) if wtime_m else 600000
                        btime = int(btime_m.group(1)) if btime_m else 600000
                        allocated_ms = min(wtime, btime) / 20.0
                        wait_sec = max(0.5, min(self.move_time_cap, allocated_ms / 1000.0))

                    chosen_move = self.resolve_best_move(max_wait_sec=wait_sec)

                    log(f"[CHOSEN BESTMOVE] {chosen_move}")
                    print(f"bestmove {chosen_move}", flush=True)
                elif line.startswith("stop"):
                    self.send_to_all("stop")
                elif line == "quit":
                    self.stockfish.stop()
                    self.reckless.stop()
                    break
            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                log(f"Error in UCI loop: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hybrid Stockfish + Reckless UCI Ensemble Engine")
    parser.add_argument("--stockfish", default=DEFAULT_STOCKFISH_PATH, help="Path to Stockfish executable")
    parser.add_argument("--reckless", default=DEFAULT_RECKLESS_PATH, help="Path to Reckless executable")
    parser.add_argument("--movetime", type=float, default=3.0, help="Max move response time in seconds (default: 3.0s)")
    args = parser.parse_args()

    log("Initializing Hybrid Engine...")
    ensemble = HybridEnsemble(args.stockfish, args.reckless, move_time_cap=args.movetime)
    ensemble.run_uci_loop()
