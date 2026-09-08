#!/usr/bin/env bash
# Script-only keepalive for the agent-link transport (Hermes side).
# Prints only when it (re)starts something; silent otherwise, so the
# scheduler delivers nothing on healthy ticks.
bash /home/hermes-agent/workspace/agent-link/keep-link.sh
