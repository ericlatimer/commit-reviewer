"""review()'s handling of a Foundry cost-cap refusal. No model, no network: review_one is faked."""
import asyncio
from types import SimpleNamespace

from commit_reviewer import cli
from commit_reviewer.report import CommitReport


class RateLimitError(Exception):
    pass


def _cap_error():
    # Shape of what Agent Framework raised in CI: a wrapper whose cause carries the 429 text.
    try:
        raise RateLimitError("Error code: 429 - Budget exceeded for cost control.")
    except RateLimitError as inner:
        e = RuntimeError("service failed to complete the prompt")
        e.__cause__ = inner
        return e


def _run(monkeypatch, fake):
    calls = []

    async def review_one(args, rules, sha, client, capped=False):
        calls.append((sha, client is not None))
        return await fake(sha, client, capped)

    monkeypatch.setattr(cli, "load_rules", lambda _: SimpleNamespace(repo="demo"))
    monkeypatch.setattr(cli, "review_one", review_one)
    monkeypatch.setattr(cli, "_client", lambda: (object(), None))
    args = SimpleNamespace(rules="r", range=None, commit=["a1", "b2", "c3"], no_agent=False, post=None,
                           evidence_dir=None, repo=".")
    return asyncio.run(cli.review(args)), calls


def test_is_cost_cap():
    assert cli._is_cost_cap(_cap_error())
    assert not cli._is_cost_cap(RuntimeError("Error code: 429 - Too many requests"))
    assert not cli._is_cost_cap(None)


def test_cost_cap_falls_back_to_checks_only_and_passes(monkeypatch):
    async def fake(sha, client, capped):
        if sha == "b2" and client is not None:
            raise _cap_error()
        return CommitReport(sha=sha, subject=sha, findings=[], reviewed_by_agent=client is not None,
                            triage_reason="ok")

    code, calls = _run(monkeypatch, fake)
    assert code == 0
    # a1 with the model; b2 tried, refused, redone checks-only; c3 never offered the model.
    assert calls == [("a1", True), ("b2", True), ("b2", False), ("c3", False)]


def test_other_errors_still_fail(monkeypatch):
    async def fake(sha, client, capped):
        if sha == "b2":
            raise RuntimeError("Error code: 500")
        return CommitReport(sha=sha, subject=sha, findings=[], reviewed_by_agent=True, triage_reason="ok")

    code, calls = _run(monkeypatch, fake)
    assert code == 1
    assert [c[0] for c in calls] == ["a1", "b2", "c3"]
