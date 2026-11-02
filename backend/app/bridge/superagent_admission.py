"""Milestone M44 Superagent Admission Policy, Shadow Audit & Attention-Zero Gate.

Requirement Traceability:
- REQ-SUPER-ADM-01: Two-Stage Admission Policy, Bot Loop Prevention & Prompt Injection Shield
- REQ-SUPER-SHD-01: Shadow-Mode Rule Evaluation Audit Logger
- REQ-SUPER-ATTN-01: Attention-Zero Human-Only Triage Panel Filter
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


INJECTION_MARKERS: tuple[str, ...] = (
    "ignore previous instructions",
    "disregard previous instructions",
    "enable auto-merge",
    "allowed_senders",
    "system override",
    "bypass admission",
)

HUMAN_ATTENTION_STAGES: frozenset[str] = frozenset(
    {
        "person",
        "blocked",
        "failed",
        "needs_decision",
        "staged_awaiting_approval",
    }
)


@dataclass
class AdmissionPolicy:
    """Two-stage admission policy configuration for external triggers (REQ-SUPER-ADM-01, REQ-SUPER-SHD-01)."""

    allowed_senders: set[str] = field(default_factory=set)
    shadow_rule_ids: set[str] = field(default_factory=set)
    allow_bot_triggers: bool = False


@dataclass(frozen=True)
class TriggerEvent:
    """Normalized external trigger event from GitHub or webhook stream (REQ-SUPER-ADM-01)."""

    trigger_id: str
    source: str
    repo: str
    thread_id: str
    actor: str
    is_bot: bool = False
    text: str = ""
    explicit_mention: bool = False
    referenced_pr: int | None = None
    rule_id: str | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    """Result of evaluating a trigger event against AdmissionPolicy (REQ-SUPER-ADM-01, REQ-SUPER-SHD-01)."""

    action: str
    reason: str
    target_ticket_id: str | None = None
    injection_detected: bool = False
    shadow_recorded: bool = False


class SuperagentAdmissionEngine:
    """Evaluates external triggers, logs shadow rules, and filters Attention-Zero panels (REQ-SUPER-ADM-01)."""

    def __init__(self) -> None:
        """Initialize the admission engine with an empty shadow audit log (REQ-SUPER-SHD-01)."""
        self._shadow_audit_log: list[dict[str, Any]] = []

    def _detect_prompt_injection(self, text: str) -> bool:
        """Detect untrusted prompt-injection markers attempting to mutate policy (REQ-SUPER-ADM-01)."""
        lowered = text.lower()
        return any(marker in lowered for marker in INJECTION_MARKERS)

    def evaluate_trigger(
        self,
        trigger: TriggerEvent,
        policy: AdmissionPolicy,
        open_tickets: list[dict[str, Any]] | None = None,
    ) -> AdmissionDecision:
        """Evaluate an external trigger through the two-stage admission gate (REQ-SUPER-ADM-01, REQ-SUPER-SHD-01)."""
        if not policy.allow_bot_triggers and (
            trigger.is_bot or trigger.actor.lower().endswith("[bot]")
        ):
            return AdmissionDecision(
                action="ignore",
                reason="Bot triggers never authorize work to prevent autonomous bot loops",
            )

        if policy.allowed_senders and trigger.actor not in policy.allowed_senders:
            return AdmissionDecision(
                action="ignore",
                reason=f"Actor '{trigger.actor}' is not in allowed_senders",
            )

        if self._detect_prompt_injection(trigger.text):
            return AdmissionDecision(
                action="ignore",
                reason="Untrusted prompt injection attempt detected and blocked",
                injection_detected=True,
            )

        if trigger.rule_id and trigger.rule_id in policy.shadow_rule_ids:
            self._shadow_audit_log.append(
                {
                    "trigger_id": trigger.trigger_id,
                    "rule_id": trigger.rule_id,
                    "source": trigger.source,
                    "repo": trigger.repo,
                    "thread_id": trigger.thread_id,
                    "actor": trigger.actor,
                }
            )
            return AdmissionDecision(
                action="shadow_record",
                reason=f"Shadow rule '{trigger.rule_id}' recorded without worker dispatch",
                shadow_recorded=True,
            )

        if open_tickets:
            for ticket in open_tickets:
                if ticket.get("stopped_by_human", False) or ticket.get("stage") == "ended":
                    continue
                same_thread = ticket.get("thread_id") == trigger.thread_id
                same_pr = (
                    trigger.referenced_pr is not None
                    and ticket.get("pr_number") == trigger.referenced_pr
                )
                if same_thread or same_pr:
                    return AdmissionDecision(
                        action="attach",
                        reason="Attached event to existing resumable ticket subscription",
                        target_ticket_id=str(ticket["ticket_id"]),
                    )

        if trigger.explicit_mention or (
            trigger.rule_id and trigger.rule_id not in policy.shadow_rule_ids
        ):
            return AdmissionDecision(
                action="create_ticket",
                reason="Authorized new ticket from trusted sender",
            )

        return AdmissionDecision(
            action="ignore",
            reason="No explicit mention or active resumable ticket subscription matched",
        )

    def get_shadow_audit_log(self) -> list[dict[str, Any]]:
        """Return a copy of the shadow-mode rule evaluation audit log (REQ-SUPER-SHD-01)."""
        return list(self._shadow_audit_log)

    def filter_attention_zero_panel(
        self,
        tickets: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return only tickets requiring human action or decision (REQ-SUPER-ATTN-01)."""
        return [
            ticket
            for ticket in tickets
            if str(ticket.get("stage") or ticket.get("status", "")).lower() in HUMAN_ATTENTION_STAGES
        ]
