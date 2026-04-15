"""Domain Intelligence API endpoints."""

from datetime import date, timedelta

from fastapi import APIRouter
from backend.db import get_db
from backend.models import (
    DomainFingerprintOut,
    OperatorClusterOut,
    DiscoveredDomainOut,
    DomainIntelRunOut,
)

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("/fingerprints", response_model=list[DomainFingerprintOut])
def list_fingerprints(competitor_id: str | None = None, shared_only: bool = False):
    """Get GA/Pixel fingerprints, optionally filtered to shared-only or by competitor."""
    db = get_db()
    q = db.table("domain_fingerprints").select("*").order("captured_at", desc=True)

    if competitor_id:
        q = q.eq("competitor_id", competitor_id)

    rows = q.execute().data

    if shared_only:
        value_counts: dict[str, int] = {}
        for row in rows:
            val = row["fingerprint_value"]
            value_counts[val] = value_counts.get(val, 0) + 1
        rows = [r for r in rows if value_counts.get(r["fingerprint_value"], 0) >= 2]

    return rows


@router.get("/clusters", response_model=list[OperatorClusterOut])
def list_clusters():
    """Get operator clusters with their member competitors."""
    db = get_db()

    clusters = (
        db.table("operator_clusters")
        .select("*")
        .order("detected_at", desc=True)
        .execute()
        .data
    )

    for cluster in clusters:
        members = (
            db.table("cluster_members")
            .select("competitor_id")
            .eq("cluster_id", cluster["id"])
            .execute()
            .data
        )
        comp_ids = [m["competitor_id"] for m in members]
        if comp_ids:
            comps = (
                db.table("competitors")
                .select("id, name, slug")
                .in_("id", comp_ids)
                .execute()
                .data
            )
            cluster["members"] = comps
        else:
            cluster["members"] = []

    return clusters


@router.get("/discovered", response_model=list[DiscoveredDomainOut])
def list_discovered(
    days: int = 30,
    status: str | None = None,
    limit: int = 100,
):
    """Get WHOIS-discovered domains, filtered by recency."""
    since = (date.today() - timedelta(days=days)).isoformat()
    db = get_db()

    q = (
        db.table("discovered_domains")
        .select("*")
        .gte("first_seen_at", since)
        .order("first_seen_at", desc=True)
        .limit(limit)
    )

    if status:
        q = q.eq("status", status)

    return q.execute().data


@router.get("/runs", response_model=list[DomainIntelRunOut])
def list_runs(limit: int = 20):
    """Get domain intel run history."""
    return (
        get_db()
        .table("domain_intel_runs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


@router.get("/stats")
def domain_stats():
    """Summary stats for the Domain Intel dashboard."""
    db = get_db()

    competitors_count = len(db.table("competitors").select("id").execute().data)
    clusters_count = len(db.table("operator_clusters").select("id").execute().data)

    week_ago = (date.today() - timedelta(days=7)).isoformat()
    new_domains_count = len(
        db.table("discovered_domains")
        .select("id")
        .gte("first_seen_at", week_ago)
        .execute()
        .data
    )

    fingerprints = db.table("domain_fingerprints").select("fingerprint_value").execute().data
    value_counts: dict[str, int] = {}
    for fp in fingerprints:
        val = fp["fingerprint_value"]
        value_counts[val] = value_counts.get(val, 0) + 1
    shared_codes = sum(1 for count in value_counts.values() if count >= 2)

    return {
        "competitors_tracked": competitors_count,
        "clusters_found": clusters_count,
        "new_domains_7d": new_domains_count,
        "shared_codes": shared_codes,
    }


@router.post("/scan", status_code=202)
def trigger_scan():
    """Trigger a domain intel extraction run."""
    db = get_db()

    db.table("domain_intel_runs").update({"status": "cancelled"}).in_(
        "status", ["pending", "running"]
    ).execute()

    run = (
        db.table("domain_intel_runs")
        .insert({"status": "pending"})
        .execute()
        .data[0]
    )

    return {"run_id": run["id"], "status": "pending"}
