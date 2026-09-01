# Contributing to ChimeraChess

Thank you for your interest in contributing! Here's everything you need to know.

---

## 🛠️ Development Setup

```bash
git clone https://github.com/YOUR_USERNAME/ChimeraChess.git
cd ChimeraChess

# No dependencies to install — pure Python stdlib
python --version  # must be 3.10+
```

## 🧪 Running Tests

Always run the full test suite before submitting a PR:

```bash
python tests/test_hybrid.py
```

All 84 tests must pass. The suite covers correctness and all security hardening measures.

## 📁 Project Structure

| Path | Purpose |
|---|---|
| `src/hybrid_engine.py` | The entire engine — orchestration, UCI loop, security |
| `tests/test_hybrid.py` | Test suite (mock-based, no engine binaries needed) |
| `scripts/run_hybrid.bat` | Windows launcher for chess GUIs |
| `scripts/run_hybrid.sh` | Linux/macOS launcher for chess GUIs |

## 🐛 Reporting Bugs

Please open a GitHub Issue with:
1. Your OS and Python version
2. Which chess GUI you are using
3. The relevant section of `hybrid_engine.log`
4. Steps to reproduce

## 💡 Feature Ideas

Good areas for contribution:
- **Pondering support** — implement `ponderhit` handling properly
- **MultiPV consensus** — use multiple PV lines for a more robust vote
- **Engine weighting** — configurable per-engine trust weights
- **Opening book bypass** — detect book positions and let engines search freely
- **Linux/macOS testing** — most development is done on Windows

## 📋 Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Add tests for any new behaviour
- Follow the existing code style (PEP 8, descriptive names)
- Update `CHANGELOG.md` with your change
- Run `python tests/test_hybrid.py` and confirm 0 failures

## 📜 License

By contributing, you agree that your contributions will be licensed under the
[GNU Affero General Public License v3.0](LICENSE).
