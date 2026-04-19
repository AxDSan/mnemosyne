#!/usr/bin/env bash
# Deploy Mnemosyne as a Hermes MemoryProvider via the plugin system.
# This creates a symlink in ~/.hermes/plugins/mnemosyne — zero Hermes core changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROVIDER_DIR="$SCRIPT_DIR/hermes_memory_provider"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET_DIR="$HERMES_HOME/plugins/mnemosyne"

echo "🚀 Mnemosyne MemoryProvider Deploy"
echo "=================================="
echo ""

if [ ! -d "$PROVIDER_DIR" ]; then
    echo "❌ Error: hermes_memory_provider/ not found at $PROVIDER_DIR"
    exit 1
fi

# Ensure plugins directory exists
mkdir -p "$HERMES_HOME/plugins"

# Remove existing symlink or directory
if [ -L "$TARGET_DIR" ]; then
    echo "🔄 Removing existing symlink: $TARGET_DIR"
    rm "$TARGET_DIR"
elif [ -d "$TARGET_DIR" ]; then
    echo "🔄 Removing existing directory: $TARGET_DIR"
    rm -rf "$TARGET_DIR"
fi

# Create symlink
ln -s "$PROVIDER_DIR" "$TARGET_DIR"
echo "✅ Symlinked: $TARGET_DIR -> $PROVIDER_DIR"

# Verify
if [ -L "$TARGET_DIR" ] && [ -d "$TARGET_DIR" ]; then
    echo "✅ Deploy verified."
else
    echo "❌ Deploy failed."
    exit 1
fi

echo ""
echo "Next steps:"
echo "  1. Set provider in config:"
echo "       hermes config set memory.provider mnemosyne"
echo ""
echo "  2. Or edit ~/.hermes/config.yaml:"
echo "       memory:"
echo "         provider: mnemosyne"
echo ""
echo "  3. Verify:"
echo "       hermes memory status"
echo "       hermes mnemosyne stats"
echo ""
