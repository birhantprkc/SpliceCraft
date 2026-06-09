# OS + Terminal Compatibility Matrix

SpliceCraft is a Textual TUI; it inherits Textual's cross-platform
support and adds a few platform-specific extras (data-dir locking,
clipboard, signal handling, file paths). This page lists every
tested OS × terminal pairing, known issues, and the env-var fixes
the launcher recommends when a capability is missing.

Last update: 2026-05-19 (SpliceCraft 0.9.7+).

---

## Quick reference

| OS / WSL | Terminal | Status | Notes |
|---|---|---|---|
| **Linux** | gnome-terminal | ✅ Fully supported | Reference platform; CI runs on Ubuntu |
| Linux | kitty | ✅ Fully supported | True-color, mouse, OSC 52 all work |
| Linux | alacritty | ✅ Fully supported | Same as kitty |
| Linux | xterm | ⚠ Limited | OSC 52 clipboard needs `allowWindowOps`; mouse drag-select may need `xtermMouseProtocol` set |
| Linux | tmux / screen | ✅ Works under | Pass `-e` (env), use `tmux`'s `set -g allow-passthrough on` for OSC 52 |
| **Raspberry Pi / ARM Linux** (64-bit) | LXTerminal / SSH | ✅ Supported (one-time toolchain) | `primer3-py` ships **no `aarch64` wheel** and primer design has no fallback, so it compiles at install — run `sudo apt install build-essential python3-dev` **once**, then `pipx install splicecraft`. `edlib` (the optional turbo aligner) also lacks an `aarch64` wheel but transparently falls back to the built-in pure-Python Myers aligner (~12× the old Biopython fallback, identical results — no build needed). All other compiled deps (`pyhmmer`/`biopython`/`Pillow`) ship `aarch64` wheels. Needs 64-bit Pi OS Bookworm+ (Python ≥3.10); Pi 4/5 ideal. See [Raspberry Pi / ARM Linux](#raspberry-pi--arm-linux) |
| Raspberry Pi / ARM Linux (32-bit) | LXTerminal / SSH | ⚠ Limited | No 32-bit-ARM wheels — `pyhmmer`/`primer3-py`/`biopython`/`Pillow` source-compile (slow). Use the 64-bit OS |
| **macOS** | Terminal.app | ✅ Fully supported | macOS 11+; older may lack true-color. **Apple Silicon + Python ≥3.10:** `primer3-py` ships no arm64 wheel for 3.10+, so it compiles at install — `xcode-select --install` once. Intel Macs use prebuilt wheels (biopython is pinned to a version that still ships them). |
| macOS | iTerm2 | ✅ Fully supported | Preferred for OSC 52 reliability |
| macOS | tmux on macOS | ✅ Works under | Same OSC 52 caveat as Linux |
| **WSL** | Windows Terminal | ✅ Fully supported | Pillow clipboard image grab disabled (Linux side) — use file picker |
| WSL | VS Code integrated | ✅ Works | VS Code's terminal handles OSC 52 |
| WSL | tmux inside WSL | ✅ Works under | Same OSC 52 caveat |
| **Windows native** | Windows Terminal | ✅ Supported | Pillow clipboard image grab available. Local HMMscan unavailable (`pyhmmer` is POSIX-only — use WSL2); BLASTN/BLASTP + everything else work natively |
| Windows native | ConPTY (cmd, PowerShell) | ⚠ Limited | Some Textual mouse modes unreliable; recommend Windows Terminal |
| Windows native | conhost.exe (legacy) | ❌ Unsupported | No true-color, no mouse — Textual will refuse to launch |

---

## Required terminal features

SpliceCraft hard-requires:

* **UTF-8 stdout encoding** — the plasmid map uses U+2800-U+28FF braille
  dots. As of 1.0.3 a non-UTF-8 terminal **no longer refuses to launch**:
  the launcher first tries to reconfigure stdout to UTF-8 (a `LANG=C`
  shell often mislabels an otherwise-capable terminal), and only if that
  genuinely fails does it fall back to a **7-bit-ASCII density-ramp map**
  that renders on any ANSI terminal. For the richer braille map, prefer a
  UTF-8 locale (`LANG=C.UTF-8` / `LANG=en_US.UTF-8`) or
  `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`. If your terminal *reports*
  UTF-8 but the font can't draw braille (boxes / blanks), force the ASCII
  map with `SPLICECRAFT_ASCII=1`.
* **256-color ANSI** — feature colors degrade visibly without it.
  Every modern terminal supports this; only ancient `vt100` doesn't.
* **Mouse events** — required for click-to-select, drag-select on
  parts bin, hover tooltips on map.
* **Alternate screen buffer** — Textual uses it for the full-screen
  TUI; without it the terminal scrollback gets polluted.

SpliceCraft soft-requires (degrades on absence):

* **True-color (24-bit) ANSI** — used for ApEinfo feature colors; on
  256-color-only terminals colors approximate to nearest palette
  entry.
* **Clipboard** — copy-to-clipboard tries, in order: a **native OS
  clipboard tool** (`wl-copy`/`xclip`/`xsel` on Linux, `pbcopy` on
  macOS, `clip`/PowerShell on Windows, `clip.exe` on WSL,
  `termux-clipboard-set` on Android) → Textual's API → OSC 52 escape →
  temp file in `<DATA_DIR>/clipboard/`. The native tier is preferred
  because it actually sets the clipboard **and reports success**, unlike
  fire-and-forget OSC 52 (which a terminal can silently ignore — e.g.
  GNOME Terminal / VTE, oversized payloads, tmux without passthrough);
  it's skipped on a headless/SSH remote, where OSC 52 is the right path.
  Install the matching tool if copy isn't working (`sudo apt install
  wl-clipboard` on Wayland, `xclip` on X11). The file tier always works.

---

## Platform-specific implementations

### Data directory lock

* **POSIX** (Linux / macOS / WSL): `fcntl.flock(LOCK_EX | LOCK_NB)`
* **Windows native**: `msvcrt.locking(LK_NBLCK, 1)`

Either failure (module missing on a stripped-down interpreter) skips
locking with a warning — the cache coherence guarantee weakens but
SpliceCraft still launches.

Stale lock detection:
* POSIX: reads `/proc/<pid>/cmdline` to confirm PID hasn't been
  recycled to an unrelated process (long-uptime systems)
* macOS / Windows: relies on `os.kill(pid, 0)` for liveness check;
  PID-recycle to unrelated process can't be detected without psutil

### Clipboard

| Operation | Linux / WSL | macOS | Windows |
|---|---|---|---|
| Copy text (Ctrl+C selection) | `wl-copy`/`xclip`/`xsel` (or `clip.exe` on WSL) → Textual → OSC 52 → file | `pbcopy` → Textual → OSC 52 → file | `clip`/PowerShell → Textual → OSC 52 (via `CONOUT$`) → file |
| Paste image (Experiments tab) | ❌ Button disabled | ✅ via Pillow `ImageGrab` | ✅ via Pillow `ImageGrab` |

Linux/WSL has no pure-Python clipboard image API; users must drop
the file via the file picker. Pillow on Win/Mac handles the
clipboard bitmap directly.

### Signals

* `SIGUSR1` faulthandler stack-dump handler installed only when
  `signal.SIGUSR1` exists (POSIX). Windows lacks it — gracefully
  skipped.
* `SIGTERM` / `SIGHUP` translated to `KeyboardInterrupt` when
  available. Windows lacks `SIGHUP`; the `getattr(_signal, name,
  None)` check filters it out.

### File permissions

* `os.chmod(path, 0o600)` for logs / bundles is skipped on Windows
  (the call is a no-op there but we avoid the syscall for clarity).

### Subprocess invocation

`subprocess.run` is always called with `cmd` as a **list** (never
`shell=True`) so Windows quoting rules and POSIX globbing don't
diverge. The update install path (`splicecraft update`) follows
this convention end-to-end.

### Optional Python dependencies

| Dep | Purpose | Degradation if missing |
|---|---|---|
| `Pillow>=10.0` | Experiments image attach, clipboard grab | Image-attach button warns; clipboard grab disabled |
| `pyspellchecker>=0.8.0` | Experiments F7 spellcheck | F7 silently no-ops |
| `primer3-py` | Primer Tm calculation | Falls back to 2+4 GC rule (less accurate) |
| `rich-pixels>=3.0.0` | Image rendering in Experiments | Image renders as placeholder |
| `pyhmmer` | In-process HMMscan | HMMscan button warns |

`Pillow`, `pyspellchecker`, `primer3-py`, and `rich-pixels` are
unconditional hard deps in `pyproject.toml`, so a normal `pip install
splicecraft` brings them on every platform. **`pyhmmer` is a hard dep
only on POSIX** (`sys_platform != 'win32'`): it ships no Windows wheels
and HMMER's C core doesn't build on native Windows, so it is
intentionally omitted there. On native Windows, HMMscan is unavailable
(the button toasts + explains the WSL2 path) while BLASTN/BLASTP fall
back to the pure-Python engine; WSL2 reports as Linux and gets pyhmmer
normally. The runtime checks also cover other edge cases (e.g. `pip
install --no-deps` for an offline minimal install).

### Raspberry Pi / ARM Linux

SpliceCraft runs well on a Raspberry Pi — it's a Linux TUI, so the
terminal side is identical to desktop Linux (and a minimal / serial
console degrades to the ASCII map; see *Required terminal features*).
Two things decide whether the install is painless:

* **Use a 64-bit OS (`aarch64`) and install a C toolchain once.** Most
  compiled deps — `pyhmmer`, `biopython`, `Pillow` — ship `aarch64`
  wheels, so they pull prebuilt binaries. But **`primer3-py` has no
  `aarch64` wheel** (upstream ships none) and primer design has no
  pure-Python fallback, so it source-compiles at install: run
  `sudo apt install build-essential python3-dev` **once** before
  `pipx install splicecraft` (it's a small, fast C extension). `edlib`
  (the optional turbo aligner) also lacks an `aarch64` wheel, but it
  transparently falls back to the built-in pure-Python Myers aligner — so
  it never needs the compiler (~12× the old Biopython fallback, identical
  results). HMMscan works (the `aarch64`
  `pyhmmer` wheel exists — the POSIX marker drops `pyhmmer` only on
  native Windows, not ARM Linux). On a **32-bit** OS (`armv7l` /
  `armhf`) none of the compiled deps have wheels, so everything
  source-compiles — possible but slow; prefer the 64-bit image. (The
  release `scripts/check_dep_wheels.py` gate tracks exactly which deps
  lack `aarch64` wheels, so this list stays honest.)
* **Python ≥ 3.10.** Raspberry Pi OS **Bookworm** (Debian 12) ships
  3.11 ✓. Older **Bullseye** ships 3.9, which trips `No matching
  distribution found for splicecraft (from versions: none)` — upgrade
  the OS or install a newer Python (e.g. via pyenv).

**Hardware.** A Pi 4 / Pi 5 (quad-core, ≥ 2 GB) is comfortable for
interactive editing and map rendering; heavy one-off operations
(building a BLAST DB, HMMscan against Pfam-A, rendering a very large
plasmid) run slower than desktop but are fine. A Pi 3 is workable; the
single-core / 512 MB boards (Pi Zero, Pi 1–2) are not recommended.
SD-card I/O makes saving large `collections.json` / `library.json`
slower — a fast card or USB-SSD boot helps. The HMM databases are heavy
to fetch + `hmmpress` (Pfam-A is ~1.5 GB) but work on a Pi 4/5 with
storage.

---

## Known issues

### Windows ConPTY / legacy `conhost.exe`

* Mouse drag-select on parts bin may not register all events under
  ConPTY. Use Windows Terminal instead.
* OSC 52 clipboard via `CONOUT$` works on Windows Terminal but not
  on `conhost.exe` (legacy console). Tier-3 file fallback kicks in.

### WSL → host clipboard

* Text copy via OSC 52 forwards through the WSL→Windows Terminal
  pipeline correctly.
* Image paste is disabled (Linux side has no pure-Python API);
  use the file picker after saving the screenshot from the Windows
  side.

### macOS Terminal.app vs iTerm2

* macOS Terminal.app's OSC 52 support is gated behind
  "Allow Mouse Reporting" + "Send escape sequences to clipboard"
  in Preferences. iTerm2 supports OSC 52 out of the box. If text
  copy doesn't reach the system clipboard, tier-3 file fallback
  still works — check `~/.local/share/splicecraft/clipboard/`.

### tmux / screen

OSC 52 passthrough requires:

```tmux
set -g allow-passthrough on
set -g set-clipboard on
```

Without these, OSC 52 escapes get filtered and tier-3 file fallback
takes over.

---

## Launch-time capability probe

On every launch, `_log_terminal_capabilities()` runs and emits a
`startup.terminal_capabilities` structured event with:

```json
{
  "encoding":       "utf-8",
  "is_tty":         true,
  "platform":       "linux",
  "blocking_count": 0,
  "warning_count":  0
}
```

Warnings (a non-UTF-8 terminal that fell back to the ASCII map,
`SPLICECRAFT_ASCII` forced on, or a missing optional dep) log and —
for the render-tier ones — surface once on stderr, but launch always
proceeds. As of 1.0.3 the probe has **no hard-blocking failures**:
encoding is rescued (reconfigure-to-UTF-8) or degraded (ASCII map)
rather than aborting, so `blocking_count` is normally `0`.

Grep your log:

```bash
grep "startup.terminal_capabilities" ~/.local/share/splicecraft/logs/splicecraft.log
```

---

## Reporting compatibility issues

If SpliceCraft refuses to launch or renders badly on your terminal:

1. Run `splicecraft logs --bundle --out splicecraft-bundle.zip`
2. Open an issue at github.com/Binomica-Labs/SpliceCraft including
   the bundle + your terminal name / version + OS version.
3. The bundle includes the last 5 UI snapshots and sanitised
   settings; sequence content NEVER ships (privacy invariant #38).
