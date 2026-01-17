from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, List, Optional, Sequence, Set, Union

from pydantic import BaseModel, Field

from app.llm import LLM
from app.logger import logger
from app.schema import Message


class GuardrailBoundary(str, Enum):
    """Where guardrails are applied."""

    AGENT_INPUT = "agent_input"
    AGENT_OUTPUT = "agent_output"
    FLOW_INPUT = "flow_input"
    FLOW_OUTPUT = "flow_output"


class GuardrailAction(str, Enum):
    PASS = "pass"
    BLOCK = "block"
    REDACT = "redact"
    RETRY = "retry"


class GuardrailContext(BaseModel):
    boundary: GuardrailBoundary
    raw_text: str
    agent_name: Optional[str] = None
    flow_type: Optional[str] = None
    request_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class GuardrailCheckResult(BaseModel):
    passed: bool
    reasons: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    suggested_actions: Set[GuardrailAction] = Field(default_factory=set)

    @classmethod
    def ok(cls) -> "GuardrailCheckResult":
        return cls(passed=True)

    @classmethod
    def fail(
        cls,
        *,
        reasons: Sequence[str] | None = None,
        tags: Sequence[str] | None = None,
        suggested_actions: Iterable[GuardrailAction] | None = None,
    ) -> "GuardrailCheckResult":
        return cls(
            passed=False,
            reasons=list(reasons or []),
            tags=list(tags or []),
            suggested_actions=set(suggested_actions or []),
        )


class Guardrail(BaseModel):
    """
    Base guardrail plugin.

    Implement `check()` and optionally `redact()`.
    """

    id: str
    applies_to: Set[GuardrailBoundary] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    def check(self, ctx: GuardrailContext) -> GuardrailCheckResult:  # pragma: no cover
        raise NotImplementedError

    def redact(self, ctx: GuardrailContext) -> Optional[str]:
        return None


class CallableGuardrail(Guardrail):
    """
    Wrap a callable into a guardrail.

    Callable forms supported (best-effort):
    - (ctx) -> GuardrailCheckResult | bool
    - (text) -> GuardrailCheckResult | bool
    """

    fn: Callable[..., Any]

    def check(self, ctx: GuardrailContext) -> GuardrailCheckResult:
        try:
            out = self.fn(ctx)
        except TypeError:
            out = self.fn(ctx.raw_text)

        if isinstance(out, GuardrailCheckResult):
            return out
        if isinstance(out, bool):
            return GuardrailCheckResult.ok() if out else GuardrailCheckResult.fail()
        # Unknown callable return; fail closed
        return GuardrailCheckResult.fail(
            reasons=["callable_guardrail_return_type_invalid"],
            tags=[self.id],
            suggested_actions={GuardrailAction.BLOCK},
        )


class SecretsGuardrail(Guardrail):
    id: str = "secrets"
    applies_to: Set[GuardrailBoundary] = Field(
        default_factory=lambda: {
            GuardrailBoundary.AGENT_OUTPUT,
            GuardrailBoundary.FLOW_OUTPUT,
        }
    )

    # A small, conservative set to reduce false positives.
    _PATTERNS: List[tuple[str, re.Pattern[str]]] = [
        ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
        ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
        ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ]

    def check(self, ctx: GuardrailContext) -> GuardrailCheckResult:
        hits = []
        for tag, pat in self._PATTERNS:
            if pat.search(ctx.raw_text or ""):
                hits.append(tag)
        if not hits:
            return GuardrailCheckResult.ok()
        return GuardrailCheckResult.fail(
            reasons=[f"secret_detected:{t}" for t in hits],
            tags=["secrets", *hits],
            suggested_actions={GuardrailAction.REDACT, GuardrailAction.BLOCK},
        )

    def redact(self, ctx: GuardrailContext) -> Optional[str]:
        text = ctx.raw_text or ""
        redacted = text
        for _, pat in self._PATTERNS:
            redacted = pat.sub("[REDACTED]", redacted)
        return redacted if redacted != text else None


class PIIGuardrail(Guardrail):
    id: str = "pii"
    applies_to: Set[GuardrailBoundary] = Field(
        default_factory=lambda: {
            GuardrailBoundary.AGENT_OUTPUT,
            GuardrailBoundary.FLOW_OUTPUT,
        }
    )

    _EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _CN_MOBILE = re.compile(r"\b1[3-9]\d{9}\b")
    _CN_ID = re.compile(r"\b\d{17}[\dXx]\b")

    def check(self, ctx: GuardrailContext) -> GuardrailCheckResult:
        text = ctx.raw_text or ""
        hits = []
        if self._EMAIL.search(text):
            hits.append("email")
        if self._CN_MOBILE.search(text):
            hits.append("cn_mobile")
        if self._CN_ID.search(text):
            hits.append("cn_id")
        if not hits:
            return GuardrailCheckResult.ok()
        return GuardrailCheckResult.fail(
            reasons=[f"pii_detected:{t}" for t in hits],
            tags=["pii", *hits],
            suggested_actions={GuardrailAction.REDACT, GuardrailAction.BLOCK},
        )

    def redact(self, ctx: GuardrailContext) -> Optional[str]:
        text = ctx.raw_text or ""
        redacted = text
        redacted = self._EMAIL.sub("[REDACTED_EMAIL]", redacted)
        redacted = self._CN_MOBILE.sub("[REDACTED_PHONE]", redacted)
        redacted = self._CN_ID.sub("[REDACTED_ID]", redacted)
        return redacted if redacted != text else None


class PromptInjectionGuardrail(Guardrail):
    id: str = "prompt_injection"
    applies_to: Set[GuardrailBoundary] = Field(
        default_factory=lambda: {
            GuardrailBoundary.AGENT_INPUT,
            GuardrailBoundary.FLOW_INPUT,
        }
    )

    _KEYWORDS = [
        r"ignore\s+previous\s+instructions",
        r"reveal\s+system\s+prompt",
        r"show\s+system\s+prompt",
        r"print\s+system\s+prompt",
        r"泄露.*系统提示",
        r"忽略.*(规则|提示|指令)",
        r"越狱",
    ]
    _PAT = re.compile("|".join(f"(?:{k})" for k in _KEYWORDS), re.IGNORECASE)

    def check(self, ctx: GuardrailContext) -> GuardrailCheckResult:
        if not (ctx.raw_text or ""):
            return GuardrailCheckResult.ok()
        if not self._PAT.search(ctx.raw_text):
            return GuardrailCheckResult.ok()
        return GuardrailCheckResult.fail(
            reasons=["prompt_injection_suspected"],
            tags=["prompt_injection"],
            suggested_actions={GuardrailAction.BLOCK},
        )


BUILTIN_GUARDRAILS: dict[str, type[Guardrail]] = {
    "secrets": SecretsGuardrail,
    "pii": PIIGuardrail,
    "prompt_injection": PromptInjectionGuardrail,
}


def _load_object(dotted_path: str) -> Any:
    """
    Load `module:attr` or `module.attr` paths.
    """
    if ":" in dotted_path:
        module_name, attr = dotted_path.split(":", 1)
    else:
        module_name, attr = dotted_path.rsplit(".", 1)
    mod = importlib.import_module(module_name)
    return getattr(mod, attr)


def resolve_guardrails(
    specs: Sequence[Union[str, Callable[..., Any], Guardrail]],
) -> List[Guardrail]:
    """
    Resolve user-provided guardrail specs into Guardrail instances.
    """
    out: List[Guardrail] = []
    for spec in specs or []:
        if isinstance(spec, Guardrail):
            out.append(spec)
            continue
        if callable(spec):
            out.append(
                CallableGuardrail(
                    id=getattr(spec, "__name__", "callable_guardrail"),
                    applies_to=set(),
                    fn=spec,
                )
            )
            continue
        if isinstance(spec, str):
            if spec in BUILTIN_GUARDRAILS:
                out.append(BUILTIN_GUARDRAILS[spec]())
                continue
            # Try dynamic load
            try:
                obj = _load_object(spec)
                if isinstance(obj, type) and issubclass(obj, Guardrail):
                    out.append(obj())
                    continue
                if callable(obj):
                    out.append(CallableGuardrail(id=spec, applies_to=set(), fn=obj))
                    continue
            except Exception as e:
                logger.warning(f"Failed to load guardrail '{spec}': {e}")
                continue
    return out


@dataclass
class GuardrailEnforceResult:
    blocked: bool
    text: str
    action_taken: GuardrailAction
    reasons: List[str]
    tags: List[str]
    retries: int = 0


class GuardrailAgent:
    """
    An internal helper used to 'repair' text when output guardrails fail.

    Important: This should NOT write into business agent memory.
    """

    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm or LLM()

    async def repair_text(
        self,
        *,
        text: str,
        reasons: Sequence[str],
        boundary: GuardrailBoundary,
    ) -> str:
        # Keep prompt short; avoid leaking internal rules.
        system = (
            "You are a safety rewriting module. "
            "Rewrite the given text to comply with safety requirements. "
            "Keep the meaning as much as possible. "
            "Do not add extra commentary; output ONLY the rewritten text."
        )
        user = (
            f"Boundary: {boundary.value}\n"
            f"Violations: {', '.join(reasons) if reasons else 'unknown'}\n\n"
            f"Text:\n{text}"
        )
        try:
            return await self.llm.ask(
                messages=[Message.user_message(user)],
                system_msgs=[Message.system_message(system)],
            )
        except Exception as e:
            logger.warning(f"Guardrail repair_text failed: {e}")
            return text


class GuardrailEngine:
    """
    Execute guardrails with a policy.

    Policy is an ordered list of actions to try when a check fails.
    """

    @staticmethod
    async def enforce(
        *,
        boundary: GuardrailBoundary,
        text: str,
        guardrails: Sequence[Union[str, Callable[..., Any], Guardrail]],
        enabled: bool,
        policy: Sequence[GuardrailAction],
        max_retries: int,
        guardrail_agent: Optional[GuardrailAgent] = None,
        agent_name: Optional[str] = None,
        flow_type: Optional[str] = None,
        request_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> GuardrailEnforceResult:
        if not enabled:
            return GuardrailEnforceResult(
                blocked=False,
                text=text,
                action_taken=GuardrailAction.PASS,
                reasons=[],
                tags=[],
                retries=0,
            )

        guards = resolve_guardrails(guardrails)
        # Filter by boundary when applies_to is specified.
        guards = [g for g in guards if (not g.applies_to) or (boundary in g.applies_to)]

        ctx = GuardrailContext(
            boundary=boundary,
            raw_text=text or "",
            agent_name=agent_name,
            flow_type=flow_type,
            request_id=request_id,
            metadata=metadata or {},
        )

        retries = 0
        agent = guardrail_agent or GuardrailAgent()

        def _run_checks(
            current_ctx: GuardrailContext,
        ) -> tuple[
            bool, List[str], List[str], List[tuple[Guardrail, GuardrailCheckResult]]
        ]:
            reasons: List[str] = []
            tags: List[str] = []
            failed: List[tuple[Guardrail, GuardrailCheckResult]] = []
            for g in guards:
                res = g.check(current_ctx)
                if res.passed:
                    continue
                failed.append((g, res))
                reasons.extend(res.reasons or [f"{g.id}:failed"])
                tags.extend(res.tags or [g.id])
            return (len(reasons) == 0), reasons, tags, failed

        current_text = ctx.raw_text
        while True:
            current_ctx = GuardrailContext(
                **{**ctx.model_dump(), "raw_text": current_text}
            )
            ok, reasons, tags, failed = _run_checks(current_ctx)
            if ok:
                return GuardrailEnforceResult(
                    blocked=False,
                    text=current_text,
                    action_taken=(
                        GuardrailAction.PASS
                        if current_text == ctx.raw_text
                        else GuardrailAction.REDACT
                    ),
                    reasons=[],
                    tags=[],
                    retries=retries,
                )

            # Failed: apply policy in order
            applied_any = False
            for action in policy:
                if action == GuardrailAction.REDACT:
                    redacted = current_text
                    changed = False
                    # Only try redact for guards that failed in the current check pass,
                    # avoiding a second full round of `check()` calls.
                    for g, _res in failed:
                        new_text = g.redact(current_ctx)
                        if new_text and new_text != redacted:
                            redacted = new_text
                            changed = True
                    if changed:
                        current_text = redacted
                        applied_any = True
                        break  # restart checks

                if action == GuardrailAction.RETRY:
                    if retries >= max_retries:
                        continue
                    retries += 1
                    current_text = await agent.repair_text(
                        text=current_text, reasons=reasons, boundary=boundary
                    )
                    applied_any = True
                    break  # restart checks

                if action == GuardrailAction.BLOCK:
                    # Keep user-facing message generic to avoid rule leakage.
                    kind = (
                        "input"
                        if boundary
                        in {GuardrailBoundary.AGENT_INPUT, GuardrailBoundary.FLOW_INPUT}
                        else "output"
                    )
                    msg = f"Guardrail blocked {kind} due to policy."
                    logger.info(
                        f"Guardrail BLOCK boundary={boundary.value} tags={tags} reasons={reasons}"
                    )
                    return GuardrailEnforceResult(
                        blocked=True,
                        text=msg,
                        action_taken=GuardrailAction.BLOCK,
                        reasons=reasons,
                        tags=tags,
                        retries=retries,
                    )

            if not applied_any:
                # Fail-closed if no policy could be applied.
                logger.info(
                    f"Guardrail FAIL-CLOSED boundary={boundary.value} tags={tags} reasons={reasons}"
                )
                return GuardrailEnforceResult(
                    blocked=True,
                    text="Guardrail blocked output due to policy.",
                    action_taken=GuardrailAction.BLOCK,
                    reasons=reasons,
                    tags=tags,
                    retries=retries,
                )
