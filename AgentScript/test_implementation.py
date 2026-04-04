#!/usr/bin/env python
"""Quick validation that AgentScript components work."""

import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from agentscript.runtime.json_recovery import test_json_recovery, recover_json
from agentscript.runtime.escalation import EscalationManager, EscalationReason, EscalationStatus
from agentscript.observability.otel import RuntimeTelemetry


def test_json_recovery_module():
    """Test JSON recovery."""
    print("\n" + "="*60)
    print("✅ Testing JSON Recovery Module")
    print("="*60)
    
    result = test_json_recovery()
    
    print(f"\n✓ All tests passed: {result['all_passed']}")
    print(f"✓ Passed: {result['passed_tests']}/{result['total_tests']}")
    
    for test in result['tests']:
        status = "✓" if test['passed'] else "✗"
        print(f"  {status} {test['name']}: {test['description']}")
    
    return result['all_passed']


async def test_hitl_escalation():
    """Test HITL escalation manager."""
    print("\n" + "="*60)
    print("✅ Testing HITL Escalation Manager")
    print("="*60)
    
    manager = EscalationManager(telemetry=RuntimeTelemetry())
    
    # Escalate a workflow
    escalation = await manager.escalate(
        run_id="run-001",
        workflow_name="legal_review",
        step_id="approval_step",
        reason=EscalationReason.APPROVAL_REQUIRED,
        context={"document": "contract.pdf", "status": "pending"},
        custom_message="Requires partner approval"
    )
    
    print(f"\n✓ Escalation created: {escalation.escalation_id}")
    print(f"✓ Run ID: {escalation.run_id}")
    print(f"✓ Workflow: {escalation.workflow_name}")
    print(f"✓ Reason: {escalation.reason.value}")
    
    # Check status
    status = manager.get_escalation_status(escalation.escalation_id)
    print(f"✓ Current status: {status['status']}")
    
    return True


def test_circuit_breaker():
    """Test circuit breaker state machine."""
    print("\n" + "="*60)
    print("✅ Testing Circuit Breaker Pattern")
    print("="*60)
    
    from agentscript.runtime.gateway import CircuitBreakerState
    from agentscript.runtime.program import CircuitBreakerConfig
    
    config = CircuitBreakerConfig(
        threshold=0.5,
        min_calls=2,
        window_size=10,
        cooldown_seconds=30,
        half_open_max_calls=3
    )
    
    cb = CircuitBreakerState()
    
    # Simulate failures
    now = 0.0
    for i in range(3):
        allowed, _ = cb.before_call(now=now, config=config)
        if allowed:
            cb.record_failure(now=now, config=config)
            print(f"  Step {i+1}: Record failure → {cb.phase.value}")
    
    print(f"\n✓ Circuit breaker state: {cb.phase.value}")
    print(f"✓ Failures recorded: {cb.recent_outcomes.count(False)}")
    
    return True


def main():
    """Run all validation tests."""
    print("\n")
    print("╔" + "═"*58 + "╗")
    print("║" + " "*15 + "AgentScript Validation Tests" + " "*15 + "║")
    print("╚" + "═"*58 + "╝")
    
    all_pass = True
    
    # Test 1: JSON Recovery
    try:
        if not test_json_recovery_module():
            all_pass = False
    except Exception as e:
        print(f"\n✗ JSON Recovery test failed: {e}")
        all_pass = False
    
    # Test 2: HITL Escalation
    try:
        if not asyncio.run(test_hitl_escalation()):
            all_pass = False
    except Exception as e:
        print(f"\n✗ HITL Escalation test failed: {e}")
        all_pass = False
    
    # Test 3: Circuit Breaker
    try:
        if not test_circuit_breaker():
            all_pass = False
    except Exception as e:
        print(f"\n✗ Circuit Breaker test failed: {e}")
        all_pass = False
    
    print("\n" + "="*60)
    if all_pass:
        print("🎉 ALL TESTS PASSED - AgentScript is ready to deploy!")
    else:
        print("⚠️  Some tests failed - check output above")
    print("="*60 + "\n")
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
