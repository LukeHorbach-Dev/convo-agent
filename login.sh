#!/usr/bin/env bash
# Sign in to Instagram in the assistant's own browser (only needed once, or when Instagram asks again).
cd "$(dirname "$0")" && exec .venv/bin/python -m assistant login
