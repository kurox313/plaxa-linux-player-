#!/usr/bin/env bash

set -e

APP_NAME="plaxa"
APP_DIR="$HOME/.local/share/plaxa"
BIN_DIR="$HOME/.local/bin"
SRC_FILE="plaxaV1.py"
DEST_FILE="$APP_DIR/plaxaV1.py"

setup_directories() {
    echo "[+] Creating directories..."
    mkdir -p "$APP_DIR"
    mkdir -p "$BIN_DIR"
}

install_plaxa() {
    echo "[+] Installing plaxa files..."
    cp "$SRC_FILE" "$DEST_FILE"
    chmod +x "$DEST_FILE"
}

create_command() {
    echo "[+] Creating plaxa command..."

    cat > "$BIN_DIR/$APP_NAME" <<EOF
#!/usr/bin/env bash
exec "\$HOME/.local/share/plaxa/plaxaV1.py" "\$@"
EOF

    chmod +x "$BIN_DIR/$APP_NAME"
}

setup_path() {
    echo "[+] Checking PATH..."

    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo "[!] ~/.local/bin not in PATH"

        if [ -f "$HOME/.bashrc" ]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
            echo "[+] Added PATH to .bashrc"
        fi

        if [ -f "$HOME/.zshrc" ]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
            echo "[+] Added PATH to .zshrc"
        fi

        if command -v fish >/dev/null 2>&1; then
            fish -c "set -U fish_user_paths \$HOME/.local/bin \$fish_user_paths"
            echo "[+] Added PATH for fish"
        fi
    fi
}

finish() {
    echo ""
    echo "[✓] plaxa installed!"
    echo ""
    echo "Run it with:"
    echo "    plaxa"
}

main() {
    setup_directories
    install_plaxa
    create_command
    setup_path
    finish
}

main
