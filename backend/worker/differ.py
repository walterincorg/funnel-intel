"""Compare a new scan run against a baseline and detect changes."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Change:
    severity: str  # critical, high, medium, low
    category: str  # funnel, pricing, structural
    step_number: int | None
    description: str


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)
    drift_level: str = "none"  # none, minor, major

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0


def diff_runs(baseline_steps: list[dict], new_steps: list[dict],
              baseline_pricing: dict | None, new_pricing: dict | None) -> DiffResult:
    result = DiffResult()

    # --- Structural: step count ---
    if len(baseline_steps) != len(new_steps):
        diff = len(new_steps) - len(baseline_steps)
        direction = "more" if diff > 0 else "fewer"
        result.changes.append(Change(
            severity="medium",
            category="structural",
            step_number=None,
            description=f"Funnel now has {abs(diff)} {direction} steps ({len(baseline_steps)} → {len(new_steps)})",
        ))

    # --- Step-by-step comparison ---
    # Coerce step_number to int: baseline comes from DB (int column) but
    # new_steps come from parsed LLM JSON where "36" vs 36 is inconsistent,
    # and `sorted()` over a mixed-type set raises TypeError.
    def _key(s: dict) -> int:
        try:
            return int(s["step_number"])
        except (KeyError, TypeError, ValueError):
            return 0

    base_map = {_key(s): s for s in baseline_steps}
    new_map = {_key(s): s for s in new_steps}
    all_steps = sorted(set(base_map.keys()) | set(new_map.keys()))

    major_drifts = 0
    for step_num in all_steps:
        bs = base_map.get(step_num)
        ns = new_map.get(step_num)

        if bs and not ns:
            result.changes.append(Change("medium", "funnel", step_num, f"Step {step_num} removed"))
        elif ns and not bs:
            q = ns.get("question_text", "unknown")
            result.changes.append(Change("medium", "funnel", step_num, f"New step {step_num}: '{q}'"))
        elif bs and ns:
            bq = bs.get("question_text", "")
            nq = ns.get("question_text", "")

            if bq and nq and bq != nq:
                # Check if it's a minor wording change or a completely different question
                overlap = len(set(bq.lower().split()) & set(nq.lower().split()))
                total = max(len(set(bq.lower().split()) | set(nq.lower().split())), 1)
                similarity = overlap / total

                if similarity < 0.3:
                    major_drifts += 1
                    result.changes.append(Change(
                        "high", "funnel", step_num,
                        f"Step {step_num} completely different question: '{bq}' → '{nq}'",
                    ))
                else:
                    result.changes.append(Change(
                        "low", "funnel", step_num,
                        f"Step {step_num} question reworded",
                    ))

            if bs.get("answer_options") != ns.get("answer_options"):
                result.changes.append(Change(
                    "medium", "funnel", step_num,
                    f"Step {step_num} answer options changed",
                ))

    # --- Pricing comparison ---
    if baseline_pricing and new_pricing:
        _diff_pricing(baseline_pricing, new_pricing, result)
    elif new_pricing and not baseline_pricing:
        result.changes.append(Change("high", "pricing", None, "Pricing now visible (was not captured before)"))
    elif baseline_pricing and not new_pricing:
        result.changes.append(Change("high", "pricing", None, "Pricing no longer visible"))

    # --- Determine drift level ---
    if major_drifts >= 3:
        result.drift_level = "major"
    elif result.has_changes:
        result.drift_level = "minor"

    return result


def _diff_pricing(base: dict, new: dict, result: DiffResult):
    base_plans = {p.get("name", ""): p for p in (base.get("plans") or [])}
    new_plans = {p.get("name", ""): p for p in (new.get("plans") or [])}

    for name in set(base_plans.keys()) | set(new_plans.keys()):
        bp = base_plans.get(name)
        np = new_plans.get(name)

        if bp and not np:
            result.changes.append(Change("high", "pricing", None, f"Plan '{name}' removed"))
        elif np and not bp:
            price = np.get("price", "?")
            result.changes.append(Change("high", "pricing", None, f"New plan '{name}' at {price}"))
        elif bp and np:
            old_price = bp.get("price")
            new_price = np.get("price")
            if old_price != new_price:
                result.changes.append(Change(
                    "high", "pricing", None,
                    f"Plan '{name}' price changed: {old_price} → {new_price}",
                ))

    base_discounts = base.get("discounts") or []
    new_discounts = new.get("discounts") or []
    if base_discounts != new_discounts:
        if new_discounts and not base_discounts:
            result.changes.append(Change("high", "pricing", None, "New discount(s) detected"))
        elif base_discounts and not new_discounts:
            result.changes.append(Change("high", "pricing", None, "Discount(s) removed"))
        else:
            result.changes.append(Change("high", "pricing", None, "Discount details changed"))
