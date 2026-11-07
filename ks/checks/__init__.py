"""Acceptance checks: one executable assertion per normative claim worth testing.

Design rules, taken from the specifications:

* A check is PASS only when a command that could have failed actually ran. Nothing here
  inspects source text to "confirm" behavior.
* Each check names the specification and section it verifies, so a failure points at the
  sentence that was violated rather than at a line of Python.
* Checks run against disposable instances. They create their own data and clean up, so
  they are safe to re-run.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Callable, Iterable

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class CheckResult:
    spec: str
    check_id: str
    title: str
    status: str
    detail: str = ""

    def line(self) -> str:
        marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[self.status]
        head = f"[{marker}] {self.check_id:<5} {self.title}"
        return head if not self.detail else f"{head}\n           {self.detail}"


@dataclass
class Context:
    """Everything a check may touch. Constructed once per run."""

    settings: object
    pg: object
    fixture: object | None = None
    available: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)


CheckFn = Callable[[Context], None]


@dataclass
class Check:
    spec: str
    check_id: str
    title: str
    fn: CheckFn
    tags: tuple[str, ...] = ()


REGISTRY: list[Check] = []


def check(spec: str, check_id: str, title: str, tags: tuple[str, ...] = ()) -> Callable[[CheckFn], CheckFn]:
    def register(fn: CheckFn) -> CheckFn:
        REGISTRY.append(Check(spec, check_id, title, fn, tags))
        return fn

    return register


class AssertionFailure(AssertionError):
    pass


def need(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionFailure(message)


class NotApplicable(Exception):
    """The target for this check is not reachable in this run. Reported as SKIP, never PASS."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise NotApplicable(message)


def needs_store(name: str) -> Callable[[CheckFn], CheckFn]:
    """Mark a check as requiring a reachable store; run_checks skips it when absent."""

    def decorate(fn: CheckFn) -> CheckFn:
        setattr(fn, "__needs_store__", name)
        return fn

    return decorate


def run_checks(context: Context, tag: str | None = None) -> list[CheckResult]:
    results: list[CheckResult] = []
    selected = [c for c in REGISTRY if tag is None or tag in c.tags or c.spec == tag]
    provisioned = bool(context.extras.get("provisioned", False))
    for c in selected:
        store = getattr(c.fn, "__needs_store__", None)
        if store and not context.available.get(store, False):
            results.append(CheckResult(c.spec, c.check_id, c.title, SKIP, f"{store} unreachable"))
            continue
        # Every run seeds a fresh workspace, so the per-workspace namespace and graph key
        # never pre-exist. A store-scoped check without --provision has no target to test,
        # and reporting that as a failure would blame the schema for a missing fixture.
        if store in ("surreal", "falkor") and not provisioned:
            results.append(
                CheckResult(c.spec, c.check_id, c.title, SKIP,
                            f"{store} locations not provisioned; rerun with --provision")
            )
            continue
        try:
            c.fn(context)
            results.append(CheckResult(c.spec, c.check_id, c.title, PASS))
        except NotApplicable as exc:
            results.append(CheckResult(c.spec, c.check_id, c.title, SKIP, str(exc)))
        except AssertionFailure as exc:
            results.append(CheckResult(c.spec, c.check_id, c.title, FAIL, str(exc)))
        except Exception as exc:  # infrastructure failure is still a failed check
            tb = traceback.format_exc(limit=3).strip().splitlines()[-1]
            results.append(
                CheckResult(c.spec, c.check_id, c.title, FAIL, f"{type(exc).__name__}: {exc} [{tb}]")
            )
    return results


def summarise(results: Iterable[CheckResult]) -> tuple[int, int, int]:
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for r in results:
        counts[r.status] += 1
    return counts[PASS], counts[FAIL], counts[SKIP]


# Importing the modules registers their checks as a side effect.
from . import cross, falkordb, postgres, surrealdb  # noqa: E402,F401
