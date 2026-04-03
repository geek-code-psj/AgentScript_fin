"""Demo helpers for AgentScript showcase workflows."""

from agentscript.demo.legal_demo import (
    LEGAL_DATA_PATH,
    LEGAL_SCRIPT_PATH,
    DemoState,
    build_demo_registry,
    find_divergence,
    run_demo,
)

__all__ = [
    "DemoState",
    "LEGAL_DATA_PATH",
    "LEGAL_SCRIPT_PATH",
    "build_demo_registry",
    "find_divergence",
    "run_demo",
]
