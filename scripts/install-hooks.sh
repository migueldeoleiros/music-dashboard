#!/bin/sh
# Point git at the tracked hooks. Run once per clone.
cd "$(git rev-parse --show-toplevel)" && git config core.hooksPath .githooks && echo "hooks enabled (.githooks)"
