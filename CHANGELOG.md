# Changelog

All notable changes to ChimeraChess are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.0.0] — 2026-09-01

### Added
- Initial public release of ChimeraChess hybrid UCI ensemble engine
- Concurrent search orchestration of Stockfish 18 and Reckless
- Smart decision arbitration algorithm:
  - Fast path when both engines agree
  - Forced-mate detection and preference
  - Score-difference threshold (50 cp) with white-perspective normalisation
  - Reckless preferred on close evaluations (aggressive NNUE style)
- Proper UCI handshake: waits for `uciok` + `readyok` from each sub-engine before declaring ready
- `isready` proxied to sub-engines — hybrid replies `readyok` only after both confirm
- `setoption` forwarding: Hash, Threads, SyzygyPath and all other options forwarded
- `go infinite` correctly handled — waits for GUI `stop` instead of timing out autonomously
- Correct `go wtime/btime` time management with increment (winc/binc) support
- Side-to-move detection for both `startpos` and `position fen` commands
- Score normalisation to white's absolute perspective for correct black-side decisions
- Stale search state reset before every new `go` command
- Thread-safety: `threading.Lock` guards all mutable per-engine search state

### Security (v1.0.0)
- **SEC-1/2** Path validation and executable safety checks
- **SEC-3** Log injection prevention via `_sanitise_for_log()`
- **SEC-4** GUI input capped at 64 KB per line
- **SEC-5** Sub-engine output bounded (32 KB/line, 4096-item queue, 4 KB PV)
- **SEC-6** Log rotation at 10 MB with 1 backup
- **SEC-7** `setoption` value sanitisation (newline/CR stripping)
- **SEC-8** UCI move regex validation on all bestmoves and PV moves
- **SEC-9** `MoveTimeCap` clamped to [0.1 s, 300 s]
- **SEC-10** Symlink protection on log file path
- **SEC-11** `_send_raw` strips embedded newlines before writing to sub-engine stdin
- **SEC-12** `close_fds=True` on all `subprocess.Popen` calls

### Fixed
- First move as white was not played (race condition during UCI startup handshake)
- `go infinite` caused hybrid to autonomously stop after `move_time_cap` seconds
- Stale centipawn/mate scores from previous position contaminated decisions
- Hardcoded `e2e4` fallback was illegal on black's turn
- Wrong time clock used (wtime) when playing as black
- FEN-based positions with black to move had incorrect side-to-move detection
- Negative mate scores (being mated) incorrectly triggered the forced-mate preference
