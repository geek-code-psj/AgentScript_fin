"""Human-In-The-Loop (HITL) runtime escalation management.

Implements deterministic escalation for workflows requiring human intervention:
- State preservation during escalation pauses
- Alert delivery with context
- Resume-from-escalation with resolution data
- Full OpenTelemetry integration for observability
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from enum import Enum
from typing import Any
import uuid

from agentscript.observability.otel import RuntimeTelemetry
from agentscript.runtime.errors import AgentScriptRuntimeError, ErrorContext


class EscalationReason(str, Enum):
    """Categories of escalation reasons."""
    
    MANUAL_REQUEST = "manual_request"
    APPROVAL_REQUIRED = "approval_required"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    RESOURCE_LIMIT = "resource_limit"
    POLICY_VIOLATION = "policy_violation"
    EXTERNAL_DEPENDENCY = "external_dependency"
    RECOVERY_FAILED = "recovery_failed"
    CUSTOM = "custom"


class EscalationStatus(str, Enum):
    """Status of an escalation."""
    
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Escalation:
    """Immutable record of an escalation event."""
    
    escalation_id: str
    run_id: str
    workflow_name: str
    step_id: str
    reason: EscalationReason
    context: dict[str, Any]
    executed_instructions: list[int]
    created_at: str
    created_by: str | None = None
    custom_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EscalationResolution:
    """Immutable resolution data provided by human."""
    
    escalation_id: str
    status: EscalationStatus
    resolved_at: str
    resolved_by: str | None = None
    decision_data: dict[str, Any] = field(default_factory=dict)
    approval_metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EscalationState:
    """Mutable storage for escalation state during runtime."""
    
    escalations: dict[str, Escalation]
    resolutions: dict[str, EscalationResolution]
    run_escalations: dict[str, str]
    
    def __init__(self) -> None:
        self.escalations = {}
        self.resolutions = {}
        self.run_escalations = {}


class EscalationAlertHandler:
    """Protocol for delivering escalation alerts."""
    
    async def send_alert(
        self,
        escalation: Escalation,
        alert_channel: str = "default",
    ) -> bool:
        raise NotImplementedError


class DefaultAlertHandler(EscalationAlertHandler):
    """No-op alert handler (logs locally only)."""
    
    async def send_alert(
        self,
        escalation: Escalation,
        alert_channel: str = "default",
    ) -> bool:
        print(
            f"[ESCALATION] {escalation.workflow_name}:{escalation.step_id} "
            f"({escalation.reason.value}) - Run: {escalation.run_id}"
        )
        return True


class EscalationManager:
    """Manages HITL escalations with state preservation and async resumption.
    
    Example:
        manager = EscalationManager(telemetry=telemetry)
        
        escalation = await manager.escalate(
            run_id="run-123",
            workflow_name="legal_review",
            step_id="s3",
            reason=EscalationReason.APPROVAL_REQUIRED,
            context={"document": "..."},
            custom_message="Requires partner approval"
        )
        
        resolution = EscalationResolution(
            escalation_id=escalation.escalation_id,
            status=EscalationStatus.RESOLVED,
            resolved_at=...,
            decision_data={"approved": True}
        )
        await manager.submit_resolution(resolution)
        
        result = await manager.resume_from_escalation(
            run_id="run-123",
            resolution=resolution
        )
    """
    
    def __init__(
        self,
        *,
        alert_handler: EscalationAlertHandler | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        """Initialize escalation manager.
        
        Args:
            alert_handler: Handler for sending escalation alerts
            telemetry: OpenTelemetry integration for observability
        """
        self.state = EscalationState()
        self.alert_handler = alert_handler or DefaultAlertHandler()
        self.telemetry = telemetry or RuntimeTelemetry()
    
    async def escalate(
        self,
        run_id: str,
        workflow_name: str,
        step_id: str,
        reason: EscalationReason | str,
        context: dict[str, Any] | None = None,
        executed_instructions: list[int] | None = None,
        created_by: str | None = None,
        custom_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Escalation:
        """Escalate a workflow to human intervention.
        
        Preserves all execution state and sends alerts.
        
        Args:
            run_id: Unique run identifier
            workflow_name: Name of workflow being escalated
            step_id: Current step ID
            reason: Why escalation occurred
            context: Workflow state snapshot
            executed_instructions: Line numbers of executed steps
            created_by: User/agent triggering escalation
            custom_message: Human-readable description
            metadata: Additional context
            
        Returns:
            Escalation record with escalation_id
            
        Raises:
            AgentScriptRuntimeError: If escalation fails
        """
        if isinstance(reason, str):
            try:
                reason_enum = EscalationReason(reason)
            except ValueError:
                reason_enum = EscalationReason.CUSTOM
        else:
            reason_enum = reason
        
        escalation_id = f"esc-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        
        escalation = Escalation(
            escalation_id=escalation_id,
            run_id=run_id,
            workflow_name=workflow_name,
            step_id=step_id,
            reason=reason_enum,
            context=context or {},
            executed_instructions=executed_instructions or [],
            created_at=now,
            created_by=created_by,
            custom_message=custom_message,
            metadata=metadata or {},
        )
        
        self.state.escalations[escalation_id] = escalation
        self.state.run_escalations[run_id] = escalation_id
        
        with self.telemetry.span(
            "escalation",
            attributes={
                "agentscript.escalation.id": escalation_id,
                "agentscript.escalation.reason": reason_enum.value,
                "agentscript.workflow.name": workflow_name,
                "agentscript.run.id": run_id,
            },
        ) as span:
            span.set_attribute("agentscript.escalation.status", "created")
        
        try:
            await self.alert_handler.send_alert(escalation)
        except Exception as e:
            self.telemetry.span(
                "escalation_alert_failed",
                attributes={
                    "agentscript.escalation.id": escalation_id,
                    "error.type": type(e).__name__,
                }
            ).__enter__().record_exception(e)
        
        return escalation
    
    async def submit_resolution(
        self,
        resolution: EscalationResolution,
    ) -> None:
        """Submit human resolution for an escalation.
        
        Args:
            resolution: Resolution decision from human
            
        Raises:
            KeyError: If escalation_id not found
            ValueError: If escalation already resolved
        """
        escalation_id = resolution.escalation_id
        
        if escalation_id not in self.state.escalations:
            raise KeyError(f"Escalation {escalation_id} not found")
        
        if escalation_id in self.state.resolutions:
            raise ValueError(
                f"Escalation {escalation_id} already has resolution"
            )
        
        self.state.resolutions[escalation_id] = resolution
        
        with self.telemetry.span(
            "escalation_resolved",
            attributes={
                "agentscript.escalation.id": escalation_id,
                "agentscript.escalation.status": resolution.status.value,
                "agentscript.escalation.resolved_by": resolution.resolved_by or "unknown",
            },
        ) as span:
            span.set_attribute("agentscript.escalation.status", "resolved")
    
    async def resume_from_escalation(
        self,
        run_id: str,
        resolution: EscalationResolution,
    ) -> dict[str, Any]:
        """Resume workflow execution after escalation.
        
        Args:
            run_id: Run identifier
            resolution: The resolution decision
            
        Returns:
            Dictionary with resumed execution result
            
        Raises:
            KeyError: If run or escalation not found
        """
        escalation_id = self.state.run_escalations.get(run_id)
        if not escalation_id:
            raise KeyError(f"No escalation found for run {run_id}")
        
        escalation = self.state.escalations[escalation_id]
        
        with self.telemetry.span(
            "escalation_resume",
            attributes={
                "agentscript.escalation.id": escalation_id,
                "agentscript.run.id": run_id,
                "agentscript.workflow.name": escalation.workflow_name,
            },
        ) as span:
            try:
                result = {
                    "success": True,
                    "escalation_id": escalation_id,
                    "run_id": run_id,
                    "decision_data": resolution.decision_data,
                    "resumed_at": datetime.now(UTC).isoformat(),
                }
                span.set_attribute("agentscript.escalation.resume_status", "success")
                
                if run_id in self.state.run_escalations:
                    del self.state.run_escalations[run_id]
                
                return result
            except Exception as e:
                span.record_exception(e)
                raise AgentScriptRuntimeError(
                    f"Failed to resume from escalation {escalation_id}",
                    context=ErrorContext(
                        run_id=run_id,
                        workflow_name=escalation.workflow_name,
                        step_id=escalation.step_id,
                    ),
                ) from e
    
    def get_escalation_status(
        self,
        escalation_id: str,
    ) -> dict[str, Any]:
        """Get current status of an escalation.
        
        Args:
            escalation_id: Escalation identifier
            
        Returns:
            Dictionary with escalation and resolution status
            
        Raises:
            KeyError: If escalation not found
        """
        escalation = self.state.escalations[escalation_id]
        resolution = self.state.resolutions.get(escalation_id)
        
        return {
            "escalation": escalation.to_dict(),
            "resolution": resolution.to_dict() if resolution else None,
            "status": resolution.status.value if resolution else EscalationStatus.PENDING.value,
        }
    
    def get_run_escalation(self, run_id: str) -> dict[str, Any] | None:
        """Get current escalation for a run, if any.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Escalation status dict, or None if no active escalation
        """
        escalation_id = self.state.run_escalations.get(run_id)
        if not escalation_id:
            return None
        
        return self.get_escalation_status(escalation_id)
    
    def list_escalations(
        self,
        status: EscalationStatus | str | None = None,
    ) -> list[dict[str, Any]]:
        """List all escalations, optionally filtered by status.
        
        Args:
            status: Filter by status (pending, resolved, etc.)
            
        Returns:
            List of escalation status dicts
        """
        result = []
        for escalation_id, escalation in self.state.escalations.items():
            resolution = self.state.resolutions.get(escalation_id)
            current_status = (
                resolution.status.value
                if resolution
                else EscalationStatus.PENDING.value
            )
            
            if status and str(status) != current_status:
                continue
            
            result.append({
                "escalation": escalation.to_dict(),
                "resolution": resolution.to_dict() if resolution else None,
                "status": current_status,
            })
        
        return result
