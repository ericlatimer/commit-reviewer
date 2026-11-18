"""Finding types shared by the deterministic checks and the review agent."""
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class AgentFinding(BaseModel):
    """What the model returns for one problem. Kept flat so strict JSON schema output works."""

    severity: Severity
    area: str = Field(description="One of the areas listed in the instructions.")
    file: str
    line: int | None = Field(description="Line in the new version of the file, or null.")
    rule: str = Field(description="Short name of the rule broken, e.g. 'admin-ops mirror'.")
    claim: str = Field(description="One sentence: what is wrong.")
    evidence: str = Field(description="The code or rule text that shows it. Quote, don't paraphrase.")


class AgentReview(BaseModel):
    findings: list[AgentFinding]
    summary: str = Field(description="One sentence on what the commit does.")


class Finding(AgentFinding):
    source: Literal["check", "agent", "gitleaks", "guardrail"] = "agent"
