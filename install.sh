#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
VENV_DIR="$REPO_DIR/.venv"

echo "==> EldenSave installer"
echo "    repo: $REPO_DIR"

# Ensure ~/.local/bin exists
mkdir -p "$BIN_DIR"

# Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not installed."
    exit 1
fi

# Create virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Upgrade pip & install editable inside venv
echo "==> Installing package in editable mode..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
"$VENV_DIR/bin/pip" install -e "$REPO_DIR"

# Create launcher as EldenSave
echo "==> Creating 'EldenSave' executable launcher..."
cat << LAUNCHER > "$BIN_DIR/EldenSave"
#!/usr/bin/env bash
exec "$VENV_DIR/bin/EldenSave" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/EldenSave"

# PATH setup
add_to_path() {
    local rcfile="$1"
    if [ -f "$rcfile" ]; then
        if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$rcfile"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rcfile"
        fi
    fi
}

add_to_path "$HOME/.bashrc"
add_to_path "$HOME/.zshrc"
add_to_path "$HOME/.profile"

export PATH="$BIN_DIR:$PATH"

echo "==> Done!"
echo ""
echo "Run:  EldenSave"
