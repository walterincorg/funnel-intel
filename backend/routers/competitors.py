import logging

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import Competitor, CompetitorCreate, CompetitorUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/competitors", tags=["competitors"])


@router.get("", response_model=list[Competitor])
def list_competitors():
    res = get_db().table("competitors").select("*").order("created_at").execute()
    return res.data


@router.get("/{competitor_id}", response_model=Competitor)
def get_competitor(competitor_id: str):
    res = get_db().table("competitors").select("*").eq("id", competitor_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Competitor not found")
    return res.data


@router.post("", response_model=Competitor, status_code=201)
def create_competitor(body: CompetitorCreate):
    res = get_db().table("competitors").insert(body.model_dump(exclude_none=True)).execute()
    log.info("Competitor created: %s (id=%s)", body.name, res.data[0]["id"])
    return res.data[0]


@router.patch("/{competitor_id}", response_model=Competitor)
def update_competitor(competitor_id: str, body: CompetitorUpdate):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(400, "No fields to update")
    res = (
        get_db()
        .table("competitors")
        .update(data)
        .eq("id", competitor_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Competitor not found")
    log.info("Competitor updated: %s fields=%s", competitor_id, list(data.keys()))
    return res.data[0]


@router.delete("/{competitor_id}", status_code=204)
def delete_competitor(competitor_id: str):
    log.info("Competitor deleted: %s", competitor_id)
    get_db().table("competitors").delete().eq("id", competitor_id).execute()
