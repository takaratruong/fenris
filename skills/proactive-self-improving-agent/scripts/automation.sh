#!/bin/bash
# Unified Automation Script for Proactive Self-Improving Agent
# Includes: Security Audit, Error Detection, and Skill Extraction Support

set +e

# Configuration
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$BASE_DIR/scripts"
LEARNINGS_DIR="$HOME/.openclaw/workspace/.learnings"
WORKSPACE_DIR="$HOME/.openclaw/workspace"

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}==>${NC} $1"; }

usage() {
    cat << EOF
Usage: $(basename "$0") <command> [options]

Commands:
  audit         Run security and environment audit
  detect        Check for errors in the last tool output (Hook target)
  extract       Create a new skill from a learning entry
  init          Initialize learnings directory and base files
  help          Show this help message

Examples:
  $(basename "$0") audit
  $(basename "$0") extract my-new-skill
EOF
}

# --- Command: Init ---
cmd_init() {
    log_step "Initializing self-improvement structure..."
    mkdir -p "$LEARNINGS_DIR"
    mkdir -p "$WORKSPACE_DIR/memory"
    
    [ -f "$LEARNINGS_DIR/LEARNINGS.md" ] || printf "# Learnings\n\nCorrections, insights, and knowledge gaps.\n\n---\n" > "$LEARNINGS_DIR/LEARNINGS.md"
    [ -f "$LEARNINGS_DIR/ERRORS.md" ] || printf "# Errors\n\nDetailed command and API failure logs.\n\n---\n" > "$LEARNINGS_DIR/ERRORS.md"
    [ -f "$LEARNINGS_DIR/FEATURE_REQUESTS.md" ] || printf "# Feature Requests\n\nRequested but missing capabilities.\n\n---\n" > "$LEARNINGS_DIR/FEATURE_REQUESTS.md"
    
    log_info "Learnings initialized in $LEARNINGS_DIR"
}

# --- Command: Audit ---
cmd_audit() {
    log_step "Running Proactive Security & Environment Audit..."
    ISSUES=0
    WARNINGS=0

    # 1. Credentials Check
    echo "📁 Checking credential files..."
    if [ -d "$WORKSPACE_DIR/.credentials" ]; then
        for f in "$WORKSPACE_DIR/.credentials"/*; do
            if [ -f "$f" ]; then
                perms=$(stat -c "%a" "$f" 2>/dev/null || stat -f "%Lp" "$f")
                if [ "$perms" != "600" ]; then
                    log_error "$f has permissions $perms (should be 600)"
                    ((ISSUES++))
                else
                    log_info "$f permissions OK (600)"
                fi
            fi
        done
    else
        echo "   No .credentials directory found"
    fi

    # 2. Secret Scan
    echo "🔍 Scanning workspace for exposed secrets..."
    SECRET_PATTERNS="(api[_-]?key|apikey|secret|password|token|auth).*[=:].{10,}"
    # Simple grep on workspace root
    matches=$(grep -rEih "$SECRET_PATTERNS" "$WORKSPACE_DIR" --exclude-dir=".git" --exclude-dir="node_modules" 2>/dev/null | grep -v "example\|template\|placeholder" | head -n 5)
    if [ -n "$matches" ]; then
        log_warn "Possible exposed secrets found. Review manually."
        ((WARNINGS++))
    else
        log_info "Secret scan complete (no obvious leaks)"
    fi

    # 3. Memory & WAL Check
    echo "📋 Verifying workspace memory protocols..."
    FILES=("AGENTS.md" "SOUL.md" "USER.md" "SESSION-STATE.md")
    for f in "${FILES[@]}"; do
        if [ ! -f "$WORKSPACE_DIR/$f" ]; then
            log_warn "Missing core memory file: $f"
            ((WARNINGS++))
        else
            log_info "Core file present: $f"
        fi
    done

    echo "----------------------------------"
    if [ $ISSUES -eq 0 ] && [ $WARNINGS -eq 0 ]; then
        log_info "Audit Passed: Environment is secure and compliant."
    else
        log_warn "Audit Finished: $ISSUES Issues, $WARNINGS Warnings."
    fi
}

# --- Command: Detect ---
cmd_detect() {
    # Expects CLAUDE_TOOL_OUTPUT or first argument
    OUTPUT="${CLAUDE_TOOL_OUTPUT:-$1}"
    if [ -z "$OUTPUT" ]; then return 0; fi

    ERROR_PATTERNS=("error:" "failed" "command not found" "No such file" "Permission denied" "fatal:" "Exception" "Traceback")
    for pattern in "${ERROR_PATTERNS[@]}"; do
        if echo "$OUTPUT" | grep -qi "$pattern"; then
            echo "<error-detected-by-proactive-agent>"
            echo "A command error was detected. Consider logging to .learnings/ERRORS.md."
            echo "Use format: [ERR-$(date +%Y%m%d)-XXX]"
            echo "</error-detected-by-proactive-agent>"
            return 1
        fi
    done
}

# --- Command: Extract ---
cmd_extract() {
    SKILL_NAME="$1"
    if [ -z "$SKILL_NAME" ]; then log_error "Skill name required"; exit 1; fi
    
    TARGET_PATH="$HOME/.openclaw/workspace/skills/$SKILL_NAME"
    if [ -d "$TARGET_PATH" ]; then log_error "Skill $SKILL_NAME already exists"; exit 1; fi
    
    mkdir -p "$TARGET_PATH"
    cat > "$TARGET_PATH/SKILL.md" << TEMPLATE
---
name: $SKILL_NAME
description: "Brief description of $SKILL_NAME"
---
# $(echo "$SKILL_NAME" | tr '-' ' ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2))}1')

## Overview
Extracted from learning loop.

## Usage
- [Step 1]
- [Step 2]
TEMPLATE
    log_info "Skill $SKILL_NAME created at $TARGET_PATH"
}

# --- Main ---
case "$1" in
    init) cmd_init ;;
    audit) cmd_audit ;;
    detect) cmd_detect "$2" ;;
    extract) cmd_extract "$2" ;;
    help|*) usage ;;
esac
