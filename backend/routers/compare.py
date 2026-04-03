from fastapi import APIRouter, HTTPException
from backend.db import get_db

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.get("/{run_a_id}/{run_b_id}")
def compare_runs(run_a_id: str, run_b_id: str):
    """Compare two scan runs step-by-step."""
    db = get_db()

    steps_a = (
        db.table("scan_steps").select("*").eq("run_id", run_a_id).order("step_number").execute().data
    )
    steps_b = (
        db.table("scan_steps").select("*").eq("run_id", run_b_id).order("step_number").execute().data
    )

    if not steps_a and not steps_b:
        raise HTTPException(404, "No steps found for either run")

    # Build comparison by matching on step_number
    max_steps = max(len(steps_a), len(steps_b))
    map_a = {s["step_number"]: s for s in steps_a}
    map_b = {s["step_number"]: s for s in steps_b}

    diffs = []
    for i in range(1, max_steps + 1):
        sa = map_a.get(i)
        sb = map_b.get(i)
        status = "unchanged"
        changes = []

        if sa and not sb:
            status = "removed"
        elif sb and not sa:
            status = "added"
        elif sa and sb:
            if sa.get("question_text") != sb.get("question_text"):
                changes.append("question_text")
            if sa.get("answer_options") != sb.get("answer_options"):
                changes.append("answer_options")
            if sa.get("step_type") != sb.get("step_type"):
                changes.append("step_type")
            if sa.get("action_taken") != sb.get("action_taken"):
                changes.append("action_taken")
            if changes:
                status = "changed"

        diffs.append({
            "step_number": i,
            "status": status,
            "changes": changes,
            "run_a": sa,
            "run_b": sb,
        })

    # Pricing comparison
    pricing_a = (
        db.table("pricing_snapshots").select("*").eq("run_id", run_a_id).execute().data
    )
    pricing_b = (
        db.table("pricing_snapshots").select("*").eq("run_id", run_b_id).execute().data
    )

    return {
        "run_a_id": run_a_id,
        "run_b_id": run_b_id,
        "total_steps_a": len(steps_a),
        "total_steps_b": len(steps_b),
        "step_diffs": diffs,
        "pricing_a": pricing_a[0] if pricing_a else None,
        "pricing_b": pricing_b[0] if pricing_b else None,
    }
