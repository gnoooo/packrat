#!/usr/bin/env bash
# Minimal Packrat installer for Linux.
# Layout: <prefix>/venv + a symlink in <bin-dir>.
set -Eeuo pipefail

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/packrat"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
PYTHON="${PYTHON:-python3}"
GIT_URL=""
GIT_REF=""
DRY_RUN=0
PIPED=0
TMP_VENV=""
OLD_VENV=""
TMP_LINK=""
if [ -n "${BASH_SOURCE[0]-}" ]; then
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
else
    # curl | bash: there is no script file; use the caller's cwd.
    SCRIPT_DIR="$(pwd)"
    PIPED=1
fi
SOURCE="$SCRIPT_DIR"

usage() {
    cat <<EOF
Usage: ./install.sh [options]

Install Packrat into a user-local venv and link the main command.

Options:
  --prefix DIR     install root (default: \$XDG_DATA_HOME/packrat or ~/.local/share/packrat)
  --bin-dir DIR    command directory (default: \$XDG_BIN_HOME or ~/.local/bin)
  --python PATH    Python >= 3.10 (default: python3)
  --git URL        install from a git remote instead of this checkout
  --ref REF        branch/tag when using --git
  --dry-run        print actions without changing anything
  -h, --help       show this help

External tools are detected but never installed automatically:
  ffmpeg/ffprobe are required; Deno >= 2.3 is strongly recommended.
  If uv is absent, the script falls back to python -m venv + pip.
  When piped (curl | bash), pass --git URL; there is no local checkout.
EOF
}

die() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

note() {
    printf 'install.sh: %s\n' "$*" >&2
}

cleanup() {
    [ -n "$TMP_VENV" ] && rm -rf "$TMP_VENV"
    # Roll back a half-finished upgrade; never delete the previous install.
    if [ -n "$OLD_VENV" ] && [ -d "$OLD_VENV" ]; then
        rm -rf "$PREFIX/venv"
        mv "$OLD_VENV" "$PREFIX/venv"
        OLD_VENV=""
    fi
    [ -n "$TMP_LINK" ] && rm -f "$TMP_LINK"
}

expand_path() {
    case "$1" in
        "~") printf '%s\n' "$HOME" ;;
        "~/"*) printf '%s/%s\n' "$HOME" "${1#\~/}" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

run() {
    if (( DRY_RUN )); then
        printf '  +'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

check_python() {
    "$PYTHON" - <<'PY' || die "Python >= 3.10 is required"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(1)
PY
}

detect_platform() {
    [ "$(uname -s)" = "Linux" ] || die "this installer currently supports Linux only"
}

detect_distro() {
    local id="" like=""
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        id="${ID:-}"
        like="${ID_LIKE:-}"
    fi
    case " $id $like " in
        *" fedora "*)
            FFMPEG_HINT="Fedora: enable RPM Fusion, then: sudo dnf install ffmpeg tmux"
            ;;
        *" debian "*|*" ubuntu "*)
            FFMPEG_HINT="Debian/Ubuntu: sudo apt install ffmpeg python3-venv tmux"
            ;;
        *" arch "*)
            FFMPEG_HINT="Arch: sudo pacman -S ffmpeg python tmux"
            ;;
        *" opensuse "*|*" suse "*)
            FFMPEG_HINT="openSUSE: sudo zypper install ffmpeg python3 tmux"
            ;;
        *)
            FFMPEG_HINT="install ffmpeg/ffprobe with your distribution package manager"
            ;;
    esac
}

check_external_tools() {
    local missing=0
    if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
        note "ffmpeg/ffprobe not found. They are required for m4a post-processing and verification."
        note "$FFMPEG_HINT"
        missing=1
    fi
    if [ -n "$GIT_URL" ]; then
        command -v git >/dev/null 2>&1 || die "git is required for --git"
    fi
    if command -v deno >/dev/null 2>&1; then
        local deno_version
        deno_version="$(deno --version 2>/dev/null | awk 'NR==1 {print $2}')" || true
        if [ -n "$deno_version" ] && ! printf '2.3.0\n%s\n' "$deno_version" | sort -V -C; then
            note "Deno $deno_version is older than 2.3.0; some tracks may fail."
        fi
    else
        note "Deno not found. It is strongly recommended for YouTube JavaScript challenges."
        note "Install from https://deno.com or your distribution package manager."
    fi
    if (( missing )); then
        die "install the required ffmpeg/ffprobe tools first (no automatic install)"
    fi
}

install_backend() {
    local venv="$1"
    if command -v uv >/dev/null 2>&1; then
        note "using uv $(uv --version 2>/dev/null | awk '{print $2}')"
        run uv venv --python "$PYTHON" "$venv"
        run uv pip install --python "$venv/bin/python" "$PIP_SOURCE"
    else
        note "uv not found; using pip fallback."
        note "For faster installs, install uv: https://docs.astral.sh/uv/getting-started/installation/"
        run "$PYTHON" -m venv "$venv"
        run "$venv/bin/python" -m pip install "$PIP_SOURCE"
    fi
}

install_packrat() {
    if (( DRY_RUN )); then
        note "dry-run: would create $PREFIX/venv and $BIN_DIR/packrat"
        return 0
    fi

    TMP_VENV="$PREFIX/.venv.new.$$"
    OLD_VENV="$PREFIX/venv.old.$$"
    TMP_LINK="$BIN_DIR/.packrat.link.$$"

    mkdir -p "$PREFIX" "$BIN_DIR"
    chmod 700 "$PREFIX" 2>/dev/null || true
    rm -rf "$TMP_VENV" "$OLD_VENV"
    trap cleanup EXIT

    install_backend "$TMP_VENV"
    "$TMP_VENV/bin/packrat" --version

    if [ -e "$PREFIX/venv" ]; then
        mv "$PREFIX/venv" "$OLD_VENV"
    fi
    local old_tmp="$TMP_VENV"
    mv "$TMP_VENV" "$PREFIX/venv"
    TMP_VENV=""

    # Console scripts created inside the temporary venv still point at its
    # old absolute path; rewrite their shebangs after the move.
    if [ -d "$PREFIX/venv/bin" ]; then
        local escaped_tmp
        escaped_tmp="$(printf '%s' "$old_tmp" | sed 's/[&|]/\\&/g')"
        while IFS= read -r script; do
            sed -i "s|$escaped_tmp|$PREFIX/venv|g" "$script"
        done < <(grep -IlF "$old_tmp" "$PREFIX/venv/bin"/* 2>/dev/null || true)
    fi

    rm -f "$TMP_LINK"
    ln -s "$PREFIX/venv/bin/packrat" "$TMP_LINK"
    mv -f "$TMP_LINK" "$BIN_DIR/packrat"
    TMP_LINK=""

    if [ -e "$OLD_VENV" ]; then
        rm -rf "$OLD_VENV"
        OLD_VENV=""
    fi
    trap - EXIT
}

main() {
    while (($#)); do
        case "$1" in
            --prefix)
                [ $# -ge 2 ] || die "--prefix needs a directory"
                PREFIX="$2"; shift 2 ;;
            --bin-dir)
                [ $# -ge 2 ] || die "--bin-dir needs a directory"
                BIN_DIR="$2"; shift 2 ;;
            --python)
                [ $# -ge 2 ] || die "--python needs a path"
                PYTHON="$2"; shift 2 ;;
            --git)
                [ $# -ge 2 ] || die "--git needs a URL"
                GIT_URL="$2"; shift 2 ;;
            --ref)
                [ $# -ge 2 ] || die "--ref needs a branch or tag"
                GIT_REF="$2"; shift 2 ;;
            --dry-run)
                DRY_RUN=1; shift ;;
            -h|--help)
                usage; exit 0 ;;
            *)
                die "unknown option: $1 (try --help)" ;;
        esac
    done

    PREFIX="$(realpath -m "$(expand_path "$PREFIX")")"
    BIN_DIR="$(realpath -m "$(expand_path "$BIN_DIR")")"
    detect_platform
    detect_distro
    require_cmd "$PYTHON"
    check_python

    if [ -n "$GIT_URL" ]; then
        if [[ "$GIT_URL" == git+* ]]; then
            PIP_SOURCE="$GIT_URL"
        else
            PIP_SOURCE="git+$GIT_URL"
        fi
        [ -n "$GIT_REF" ] && PIP_SOURCE="$PIP_SOURCE@$GIT_REF"
    else
        if (( PIPED )); then
            die "when piping install.sh, pass --git URL (there is no checkout to install from)"
        fi
        [ -f "$SOURCE/pyproject.toml" ] || die "pyproject.toml not found; run from a checkout or pass --git URL"
        PIP_SOURCE="$SOURCE"
    fi

    note "install root: $PREFIX"
    note "command:      $BIN_DIR/packrat"
    note "source:       $PIP_SOURCE"
    check_external_tools
    install_packrat

    if (( DRY_RUN )); then
        note "dry-run complete; no changes made"
        exit 0
    fi
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) note "add this to PATH: export PATH=\"$BIN_DIR:\$PATH\"" ;;
    esac
    note "installed: $("$BIN_DIR/packrat" --version 2>/dev/null || echo 'packrat')"
    note "next: packrat login"
}

main "$@"
