#!/usr/bin/env python3
"""
ChimeraChess — Automated Engine Setup
======================================
Downloads Stockfish 18 and Reckless into the engines/ directory.
Automatically detects your OS and CPU capabilities (AVX-512 / AVX2 / generic).

Usage:
    python setup.py

After running, launch the hybrid engine with:
    python src/hybrid_engine.py
    -- OR --
    scripts/run_hybrid.bat  (Windows)
    scripts/run_hybrid.sh   (Linux / macOS)
"""

import sys
import os
import platform
import struct
import subprocess
import urllib.request
import urllib.error
import zipfile
import tarfile
import shutil
import stat
import json
import hashlib
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENGINES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines")

# Stockfish 18 release asset names per platform
# Source: https://github.com/official-stockfish/Stockfish/releases/tag/sf_18
STOCKFISH_VERSION = "sf_18"
STOCKFISH_BASE_URL = (
    "https://github.com/official-stockfish/Stockfish/releases/download/"
    f"{STOCKFISH_VERSION}/"
)
STOCKFISH_ASSETS = {
    # (system, machine, avx_level) -> (asset_filename, binary_name_inside_zip)
    ("windows", "amd64",  "avx512"):  ("stockfish-windows-x86-64-avx2.zip",   "stockfish/stockfish-windows-x86-64-avx2.exe"),
    ("windows", "amd64",  "avx2"):    ("stockfish-windows-x86-64-avx2.zip",   "stockfish/stockfish-windows-x86-64-avx2.exe"),
    ("windows", "amd64",  "generic"): ("stockfish-windows-x86-64.zip",        "stockfish/stockfish-windows-x86-64.exe"),
    ("linux",   "x86_64", "avx512"):  ("stockfish-ubuntu-x86-64-avx2.tar",    "stockfish/stockfish-ubuntu-x86-64-avx2"),
    ("linux",   "x86_64", "avx2"):    ("stockfish-ubuntu-x86-64-avx2.tar",    "stockfish/stockfish-ubuntu-x86-64-avx2"),
    ("linux",   "x86_64", "generic"): ("stockfish-ubuntu-x86-64.tar",         "stockfish/stockfish-ubuntu-x86-64"),
    ("linux",   "aarch64","generic"): ("stockfish-ubuntu-armv8.tar",          "stockfish/stockfish-ubuntu-armv8"),
    ("darwin",  "x86_64", "avx2"):    ("stockfish-macos-x86-64-avx2.tar",     "stockfish/stockfish-macos-x86-64-avx2"),
    ("darwin",  "x86_64", "generic"): ("stockfish-macos-x86-64.tar",          "stockfish/stockfish-macos-x86-64"),
    ("darwin",  "arm64",  "generic"): ("stockfish-macos-m1-apple-silicon.tar","stockfish/stockfish-macos-m1-apple-silicon"),
}

# Reckless latest release
# We query the GitHub API to get the latest version dynamically
RECKLESS_API_URL = "https://api.github.com/repos/codedeliveryservice/Reckless/releases/latest"
RECKLESS_ASSET_PATTERNS = {
    ("windows", "amd64",  "avx512"):  "reckless-{ver}-x86_64-pc-windows-msvc-avx512.zip",
    ("windows", "amd64",  "avx2"):    "reckless-{ver}-x86_64-pc-windows-msvc-avx2.zip",
    ("windows", "amd64",  "generic"): "reckless-{ver}-x86_64-pc-windows-msvc.zip",
    ("linux",   "x86_64", "avx512"):  "reckless-{ver}-x86_64-unknown-linux-gnu-avx512.tar.gz",
    ("linux",   "x86_64", "avx2"):    "reckless-{ver}-x86_64-unknown-linux-gnu-avx2.tar.gz",
    ("linux",   "x86_64", "generic"): "reckless-{ver}-x86_64-unknown-linux-gnu.tar.gz",
    ("linux",   "aarch64","generic"): "reckless-{ver}-aarch64-unknown-linux-gnu.tar.gz",
    ("darwin",  "x86_64", "avx2"):    "reckless-{ver}-x86_64-apple-darwin-avx2.tar.gz",
    ("darwin",  "x86_64", "generic"): "reckless-{ver}-x86_64-apple-darwin.tar.gz",
    ("darwin",  "arm64",  "generic"): "reckless-{ver}-aarch64-apple-darwin.tar.gz",
}


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def _c(text, code): return f"\033[{code}m{text}\033[0m"
def green(t):  return _c(t, "92")
def yellow(t): return _c(t, "93")
def red(t):    return _c(t, "91")
def bold(t):   return _c(t, "1")
def cyan(t):   return _c(t, "96")

# Disable colour on Windows cmd if not supported
if platform.system() == "Windows":
    os.system("")  # enable ANSI on Windows terminal


def banner():
    print()
    print(bold(cyan("  ╔══════════════════════════════════════╗")))
    print(bold(cyan("  ║    ChimeraChess — Engine Setup       ║")))
    print(bold(cyan("  ╚══════════════════════════════════════╝")))
    print()


def step(msg):
    print(f"\n{bold('▶')} {msg}")


def ok(msg):
    print(f"  {green('✔')} {msg}")


def warn(msg):
    print(f"  {yellow('⚠')}  {msg}")


def err(msg):
    print(f"  {red('✘')} {msg}")


# ---------------------------------------------------------------------------
# CPU capability detection
# ---------------------------------------------------------------------------

def _cpuid(leaf, subleaf=0):
    """Run CPUID via Python's ctypes on x86/x86_64. Returns (eax,ebx,ecx,edx)."""
    try:
        import ctypes
        if platform.system() == "Windows":
            lib = ctypes.windll.kernel32
            # Use __cpuidex via inline assembly is tricky; fall back to subprocess
            raise NotImplementedError
        # On Linux/macOS we can try a small C snippet via cffi or subprocess
        raise NotImplementedError
    except Exception:
        return None


def detect_cpu_level():
    """
    Returns 'avx512', 'avx2', or 'generic'.
    Uses /proc/cpuinfo on Linux, sysctl on macOS, and wmic/powershell on Windows.
    Falls back to 'avx2' on x86_64 (safe assumption for modern CPUs made after 2013).
    """
    system  = platform.system().lower()
    machine = platform.machine().lower()

    if machine not in ("amd64", "x86_64"):
        return "generic"   # ARM etc. — no AVX

    flags = set()

    try:
        if system == "linux":
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("flags"):
                        flags = set(line.split(":")[1].lower().split())
                        break
        elif system == "darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.features",
                 "machdep.cpu.leaf7_features"],
                stderr=subprocess.DEVNULL, text=True
            )
            flags = set(out.lower().split())
        elif system == "windows":
            out = subprocess.check_output(
                ["powershell", "-Command",
                 "Get-WmiObject -Class Win32_Processor | "
                 "Select-Object -ExpandProperty Name"],
                stderr=subprocess.DEVNULL, text=True
            )
            # Heuristic: modern Intel/AMD CPUs with AVX2 (Haswell+, Zen+)
            # We'll try a more reliable method below
            pass
    except Exception:
        pass

    # Windows: check via __cpuid-based Python snippet
    if system == "windows":
        try:
            import ctypes
            arr = (ctypes.c_int * 4)()
            # __cpuidex(arr, 7, 0) - Extended Features
            ctypes.windll.kernel32  # just ensure windll accessible
            # Actually call __cpuid via a tiny inline asm trick is hard;
            # use a known-good subprocess approach:
            ps_script = (
                "[System.Runtime.Intrinsics.X86.Avx512F]::IsSupported; "
                "[System.Runtime.Intrinsics.X86.Avx2]::IsSupported"
            )
            out = subprocess.check_output(
                ["powershell", "-Command", ps_script],
                stderr=subprocess.DEVNULL, text=True
            ).strip().splitlines()
            if len(out) >= 2:
                avx512_ok = out[0].strip().lower() == "true"
                avx2_ok   = out[1].strip().lower() == "true"
                if avx512_ok:
                    return "avx512"
                if avx2_ok:
                    return "avx2"
                return "generic"
        except Exception:
            pass
        # Fallback: assume AVX2 (true for any CPU since ~2013)
        return "avx2"

    if "avx512f" in flags or "avx512" in flags:
        return "avx512"
    if "avx2" in flags:
        return "avx2"
    return "generic"


def detect_platform():
    """Returns (system, machine, avx_level) normalised for our asset maps."""
    system  = platform.system().lower()
    machine = platform.machine().lower()
    if machine == "amd64":
        machine = "amd64"      # Windows reports AMD64
    elif machine == "x86_64":
        machine = "x86_64"
    elif machine in ("arm64", "aarch64"):
        machine = "arm64" if system == "darwin" else "aarch64"

    avx = detect_cpu_level()
    return system, machine, avx


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _progress_hook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        bar_len = 30
        filled  = bar_len * pct // 100
        bar     = "█" * filled + "░" * (bar_len - filled)
        mb_done = downloaded / 1_048_576
        mb_tot  = total_size / 1_048_576
        print(
            f"\r    [{bar}] {pct:3d}%  {mb_done:.1f}/{mb_tot:.1f} MB",
            end="", flush=True
        )
    else:
        mb = block_num * block_size / 1_048_576
        print(f"\r    Downloaded {mb:.1f} MB", end="", flush=True)


def download_file(url, dest_path):
    """Download *url* to *dest_path*, showing a progress bar."""
    print(f"    {cyan(url)}")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ChimeraChess-Setup/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, \
             open(dest_path, "wb") as out:
            total = int(resp.headers.get("Content-Length", 0))
            block = 65536
            downloaded = 0
            block_num  = 0
            while True:
                data = resp.read(block)
                if not data:
                    break
                out.write(data)
                downloaded += len(data)
                block_num  += 1
                _progress_hook(block_num, block, total)
        print()   # newline after progress bar
        return True
    except urllib.error.HTTPError as e:
        print()
        err(f"HTTP {e.code}: {e.reason}  —  {url}")
        return False
    except Exception as e:
        print()
        err(f"Download failed: {e}")
        return False


def extract_single_binary(archive_path, member_name, dest_path):
    """
    Extract one specific file from a zip or tar archive.
    *member_name* is the path inside the archive.
    *dest_path* is where to write the binary.
    """
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Find the member (case-insensitive on Windows)
            names = zf.namelist()
            match = next(
                (n for n in names if n.lower() == member_name.lower()),
                None
            )
            if not match:
                # Try just the basename
                base = os.path.basename(member_name).lower()
                match = next(
                    (n for n in names if os.path.basename(n).lower() == base),
                    None
                )
            if not match:
                err(f"Could not find '{member_name}' inside archive.")
                err(f"Archive contents: {names[:10]}")
                return False
            data = zf.read(match)
    elif archive_path.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2")):
        mode = "r:*"
        with tarfile.open(archive_path, mode) as tf:
            names = tf.getnames()
            match = next(
                (n for n in names if n.lower() == member_name.lower()),
                None
            )
            if not match:
                base = os.path.basename(member_name).lower()
                match = next(
                    (n for n in names
                     if os.path.basename(n).lower() == base),
                    None
                )
            if not match:
                err(f"Could not find '{member_name}' inside archive.")
                err(f"Archive contents: {names[:10]}")
                return False
            member = tf.getmember(match)
            f = tf.extractfile(member)
            data = f.read()
    else:
        err(f"Unknown archive format: {archive_path}")
        return False

    with open(dest_path, "wb") as out:
        out.write(data)

    # Make executable on POSIX
    if platform.system() != "Windows":
        st = os.stat(dest_path)
        os.chmod(dest_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return True


# ---------------------------------------------------------------------------
# Stockfish download
# ---------------------------------------------------------------------------

def setup_stockfish(system, machine, avx):
    step("Setting up Stockfish 18")

    # Determine output binary name
    ext = ".exe" if system == "windows" else ""
    dest_bin = os.path.join(ENGINES_DIR, f"stockfish{ext}")

    if os.path.isfile(dest_bin):
        ok(f"Stockfish already present at {dest_bin}")
        return dest_bin

    # Look up asset
    key = (system, machine, avx)
    if key not in STOCKFISH_ASSETS:
        # Try generic fallback
        key = (system, machine, "generic")
    if key not in STOCKFISH_ASSETS:
        err(f"No Stockfish build available for platform: {system}/{machine}/{avx}")
        return None

    asset_name, member_name = STOCKFISH_ASSETS[key]
    url = STOCKFISH_BASE_URL + asset_name
    archive_path = os.path.join(ENGINES_DIR, asset_name)

    print(f"  Downloading {bold(asset_name)} ...")
    if not download_file(url, archive_path):
        return None

    print(f"  Extracting binary ...")
    if not extract_single_binary(archive_path, member_name, dest_bin):
        return None

    # Clean up archive
    os.remove(archive_path)

    ok(f"Stockfish installed → {dest_bin}")
    return dest_bin


# ---------------------------------------------------------------------------
# Reckless download
# ---------------------------------------------------------------------------

def _fetch_reckless_release():
    """Query GitHub API for the latest Reckless release metadata."""
    print(f"  Querying GitHub API for latest Reckless release ...")
    try:
        req = urllib.request.Request(
            RECKLESS_API_URL,
            headers={"User-Agent": "ChimeraChess-Setup/1.0",
                     "Accept":     "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data
    except Exception as e:
        err(f"Could not fetch Reckless release info: {e}")
        return None


def setup_reckless(system, machine, avx):
    step("Setting up Reckless")

    ext = ".exe" if system == "windows" else ""
    dest_bin = os.path.join(ENGINES_DIR, f"reckless{ext}")

    if os.path.isfile(dest_bin):
        ok(f"Reckless already present at {dest_bin}")
        return dest_bin

    release = _fetch_reckless_release()
    if not release:
        return None

    version = release.get("tag_name", "v0.9.0")   # e.g. "v0.9.0"
    ver_str = version.lstrip("v")
    assets  = {a["name"]: a["browser_download_url"]
               for a in release.get("assets", [])}

    # Find the right asset, trying avx levels from best to worst
    avx_order = {
        "avx512":  ["avx512", "avx2", "generic"],
        "avx2":    ["avx2",   "generic"],
        "generic": ["generic"],
    }.get(avx, ["generic"])

    key = (system, machine, avx)
    chosen_url  = None
    chosen_name = None

    for level in avx_order:
        k = (system, machine, level)
        pattern = RECKLESS_ASSET_PATTERNS.get(k)
        if not pattern:
            continue
        asset_name = pattern.format(ver=ver_str)
        if asset_name in assets:
            chosen_url  = assets[asset_name]
            chosen_name = asset_name
            break

    if not chosen_url:
        # Last resort: search for any matching asset by prefix
        prefix = f"reckless-{ver_str}-"
        for name, url in assets.items():
            if name.startswith(prefix) and (
                ("windows" in name if system == "windows" else True)
            ):
                chosen_url  = url
                chosen_name = name
                warn(f"Exact match not found — falling back to: {name}")
                break

    if not chosen_url:
        err(
            f"No Reckless asset found for {system}/{machine}/{avx}.\n"
            f"  Available assets: {list(assets.keys())}"
        )
        return None

    archive_path = os.path.join(ENGINES_DIR, chosen_name)
    print(f"  Downloading {bold(chosen_name)} ...")
    if not download_file(chosen_url, archive_path):
        return None

    # Binary name inside the archive is usually just "reckless" or "reckless.exe"
    member_name = f"reckless{ext}"
    print("  Extracting binary ...")
    if not extract_single_binary(archive_path, member_name, dest_bin):
        return None

    os.remove(archive_path)

    ok(f"Reckless installed → {dest_bin}")
    return dest_bin


# ---------------------------------------------------------------------------
# Smoke test — verify both engines respond to UCI
# ---------------------------------------------------------------------------

def smoke_test(bin_path, label):
    """Send 'uci\\nquit\\n' and check for 'uciok' in the response."""
    try:
        proc = subprocess.Popen(
            [bin_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        stdout, _ = proc.communicate(input="uci\nquit\n", timeout=10)
        if "uciok" in stdout:
            ok(f"{label} smoke test passed (responded with uciok)")
            return True
        else:
            err(f"{label} did not respond with uciok. Output: {stdout[:200]}")
            return False
    except Exception as e:
        err(f"{label} smoke test failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Update launcher scripts to point to new engines/ paths
# ---------------------------------------------------------------------------

def update_launchers(sf_path, rk_path):
    """Rewrite the launcher scripts to use the newly downloaded binaries."""
    step("Updating launcher scripts")

    sf_rel = os.path.relpath(sf_path, os.path.dirname(os.path.abspath(__file__)))
    rk_rel = os.path.relpath(rk_path, os.path.dirname(os.path.abspath(__file__)))
    engine_rel = os.path.join("src", "hybrid_engine.py")

    # Windows .bat
    bat = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "scripts", "run_hybrid.bat"
    )
    if os.path.isfile(bat):
        content = (
            "@echo off\n"
            ":: ChimeraChess — auto-configured by setup.py\n"
            "setlocal\n"
            'set "SCRIPT_DIR=%~dp0"\n'
            f'python "%SCRIPT_DIR%..\\{engine_rel}" '
            f'--stockfish "%SCRIPT_DIR%..\\{sf_rel}" '
            f'--reckless "%SCRIPT_DIR%..\\{rk_rel}" %*\n'
        )
        with open(bat, "w") as f:
            f.write(content)
        ok(f"Updated {bat}")

    # Shell script
    sh = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "scripts", "run_hybrid.sh"
    )
    if os.path.isfile(sh):
        sf_rel_posix = sf_rel.replace("\\", "/")
        rk_rel_posix = rk_rel.replace("\\", "/")
        engine_posix = engine_rel.replace("\\", "/")
        content = (
            '#!/usr/bin/env bash\n'
            '# ChimeraChess — auto-configured by setup.py\n'
            'set -euo pipefail\n'
            'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            'ROOT="$SCRIPT_DIR/.."\n'
            f'exec python3 "$ROOT/{engine_posix}" '
            f'--stockfish "$ROOT/{sf_rel_posix}" '
            f'--reckless "$ROOT/{rk_rel_posix}" "$@"\n'
        )
        with open(sh, "w") as f:
            f.write(content)
        # make executable
        st = os.stat(sh)
        os.chmod(sh, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        ok(f"Updated {sh}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    banner()

    # Detect platform
    system, machine, avx = detect_platform()
    print(f"  Platform : {bold(platform.system())} / {platform.machine()}")
    print(f"  CPU AVX  : {bold(avx.upper())}")
    print(f"  Python   : {bold(sys.version.split()[0])}")

    if sys.version_info < (3, 10):
        err("Python 3.10 or newer is required.")
        sys.exit(1)

    # Create engines/ directory
    os.makedirs(ENGINES_DIR, exist_ok=True)

    sf_path = setup_stockfish(system, machine, avx)
    rk_path = setup_reckless(system, machine, avx)

    print()
    step("Smoke testing engines")

    sf_ok = smoke_test(sf_path, "Stockfish") if sf_path else False
    rk_ok = smoke_test(rk_path, "Reckless")  if rk_path else False

    if sf_ok or rk_ok:
        if sf_path and rk_path:
            update_launchers(sf_path, rk_path)

        print()
        print(bold(green("  ══════════════════════════════════════")))
        print(bold(green("    Setup complete! 🎉")))
        print(bold(green("  ══════════════════════════════════════")))
        print()
        print("  Run the engine:")
        print(f"    {cyan('python src/hybrid_engine.py')}")
        print()
        print("  Or register in your chess GUI:")
        print(f"    Windows : {cyan('scripts/run_hybrid.bat')}")
        print(f"    Linux   : {cyan('scripts/run_hybrid.sh')}")
        print()
    else:
        err("Setup encountered errors. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
