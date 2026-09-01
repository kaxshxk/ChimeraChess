<div align="center">

# ♟️ ChimeraChess

### A Security-Hardened Hybrid UCI Chess Engine

**Stockfish 18 × Reckless** — Two world-class engines. One unstoppable hybrid.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![UCI](https://img.shields.io/badge/Protocol-UCI-green)](https://www.shredderchess.com/chess-features/uci-universal-chess-interface.html)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Tests](https://github.com/YOUR_USERNAME/ChimeraChess/actions/workflows/test.yml/badge.svg)](https://github.com/YOUR_USERNAME/ChimeraChess/actions/workflows/test.yml)
[![Engines](https://img.shields.io/badge/Engines-Stockfish%2018%20%2B%20Reckless-orange)](https://github.com/official-stockfish/Stockfish)

</div>

---

## 🧬 What is ChimeraChess?

**ChimeraChess** is a UCI-compliant ensemble chess engine that orchestrates **Stockfish 18** and **Reckless** simultaneously, fusing their evaluations into a single, stronger decision.

Unlike a single engine, ChimeraChess:
- Runs both engines **concurrently** on every position
- Uses a smart **decision arbitration** algorithm to pick the best move
- Falls back gracefully when one engine is unavailable
- Behaves as a **drop-in UCI engine** in any chess GUI (CuteChess, Arena, En Croissant, etc.)

```
  CuteChess / Arena / Any UCI GUI
            │
            │ UCI protocol
            ▼
     ┌─────────────────┐
     │  ChimeraChess   │  ← hybrid_engine.py  (this project)
     │  Orchestrator   │
     └────────┬────────┘
              │
      ┌───────┴────────┐
      ▼                ▼
 Stockfish 18       Reckless
 (C++, NNUE)     (Rust, NNUE)
   ~3600 Elo       ~3833 Elo
```

---

## ⚡ Decision Logic

| Condition | Chosen Move |
|---|---|
| Both engines agree | That move (fast path) |
| Stockfish sees forced mate | Stockfish's move |
| Reckless sees forced mate | Reckless's move |
| Score difference > 50 cp | Higher-scoring engine's move |
| Scores within 50 cp | Reckless's move (more aggressive NNUE) |
| Only one engine responded | That engine's move |
| No bestmove (fallback) | First valid move from PV |

Scores are always compared from **white's absolute perspective** — even when playing as black — preventing the wrong engine from being preferred.

---

## 🚀 Getting Started

### Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Stockfish | 18 (AVX2 recommended) |
| Reckless | v0.9.0+ |

No third-party Python packages required — uses only the standard library.

### Directory Layout

```
ChimeraChess/
├── src/
│   └── hybrid_engine.py     ← the engine (run this)
├── tests/
│   └── test_hybrid.py       ← full test suite (84 tests)
├── scripts/
│   ├── run_hybrid.bat        ← Windows launcher for chess GUIs
│   └── run_hybrid.sh         ← Linux/macOS launcher
├── .github/
│   └── workflows/
│       └── test.yml          ← CI pipeline
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
└── .gitignore
```

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/ChimeraChess.git
cd ChimeraChess

# 2. Run the automated setup — downloads everything for you
python setup.py
```

That's it. `setup.py` will:
- Detect your OS and CPU (AVX-512 / AVX2 / generic)
- Check if Stockfish/Reckless are already present in `engines/` (skips downloading if found)
- Download missing builds from official releases if needed
- Smoke-test both engines
- Update the launcher scripts with the correct paths

**Already have Stockfish & Reckless?**
If you already have your own binaries installed on your system, you don't need to re-download them:
- **Option 1 (Copy):** Copy your existing `stockfish` and `reckless` binaries into the `engines/` directory before running `python setup.py`. The script will detect them and skip downloading.
- **Option 2 (CLI flag):** Pass the absolute path to your binaries directly when running:
  ```bash
  python src/hybrid_engine.py --stockfish /path/to/stockfish --reckless /path/to/reckless
  ```
- **Option 3 (GUI settings):** Configure `StockfishPath` and `RecklessPath` options in your UCI Chess GUI settings.


### Registering in a Chess GUI (CuteChess example)

1. Open **CuteChess → Engines → Manage Engines → Add**
2. Set the **command** to the full path of `scripts/run_hybrid.bat` (Windows) or `scripts/run_hybrid.sh` (Linux/macOS)
3. Set the **working directory** to the repository root
4. Click **Detect** — the GUI will discover the engine automatically

---

## ⚙️ UCI Options

| Option | Type | Default | Description |
|---|---|---|---|
| `StockfishPath` | string | auto-detected | Path to the Stockfish executable |
| `RecklessPath` | string | auto-detected | Path to the Reckless executable |
| `Hash` | spin | 16 MB | Transposition table size (forwarded to both engines) |
| `Threads` | spin | 1 | Search threads per engine |
| `MoveTimeCap` | spin | 5 s | Maximum seconds allocated per move [0.1–300] |

### Command-line Arguments

```
python src/hybrid_engine.py [OPTIONS]

  --stockfish PATH    Path to Stockfish executable (default: auto)
  --reckless  PATH    Path to Reckless executable  (default: auto)
  --movetime  SECS    Default max move time in seconds (default: 5.0)
```

---

## 🔒 Security

ChimeraChess is designed to be safe even when exposed to untrusted GUIs or engine outputs:

| Protection | Detail |
|---|---|
| **Path validation** | Engine paths are resolved to real absolute paths; null bytes and newlines rejected |
| **Executable check** | Binaries must be regular files with execute permission |
| **Log injection prevention** | All user-controlled strings are sanitised before logging |
| **Input length limiting** | GUI input capped at 64 KB per line |
| **Engine output limiting** | Sub-engine lines capped at 32 KB; stdout queue bounded at 4096 items |
| **Log rotation** | Log rotated at 10 MB; 1 backup kept |
| **setoption sanitisation** | Option values have embedded newlines/CRs stripped before forwarding |
| **Move validation** | All bestmoves validated against UCI move regex before use |
| **MoveTimeCap bounds** | Clamped to [0.1 s, 300 s] — prevents GUI from locking the process |
| **Symlink protection** | Symlinks at the log path are removed on startup |
| **Command injection** | `_send_raw` strips embedded newlines before writing to engine stdin |
| **FD isolation** | Sub-engines launched with `close_fds=True` |

---

## 🧪 Running Tests

```bash
python tests/test_hybrid.py
```

Expected output:
```
Results: 84 passed, 0 failed out of 84 tests
ALL TESTS PASSED
```

The test suite covers:
- Side-to-move detection (startpos, FEN, move lists)
- Score normalisation (white vs black perspective)
- Move arbitration logic (all decision branches)
- Time management (movetime, wtime/btime, increment, depth)
- State reset isolation between moves
- UCI line parsing (all message types)
- All 12 security hardening measures

---

## 📄 License

This project is licensed under the **GNU Affero General Public License v3.0** — see [LICENSE](LICENSE) for details.

This is required for compatibility with:
- [Stockfish](https://github.com/official-stockfish/Stockfish) — GPL v3
- [Reckless](https://github.com/codedeliveryservice/Reckless) — AGPL v3

---

## 🙏 Acknowledgements

- [**Stockfish**](https://github.com/official-stockfish/Stockfish) — the world's strongest open-source chess engine
- [**Reckless**](https://github.com/codedeliveryservice/Reckless) — a top-competitive engine rated ~3833 Elo
- [**Chess Programming Wiki**](https://www.chessprogramming.org/) — invaluable reference for UCI protocol details
- [**CuteChess**](https://github.com/cutechess/cutechess) — the GUI used for testing and tournament play

---

<div align="center">
Made with ♟️ — <em>Two engines. One Chimera.</em>
</div>
