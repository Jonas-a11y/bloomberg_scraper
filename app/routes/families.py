import threading

from fastapi import APIRouter, HTTPException, Query

from app import family

router = APIRouter()


@router.post("/families/refresh")
def refresh():
    if family.is_running():
        return {"status": "already_running", **family.get_state()}
    threading.Thread(target=family.run_refresh, daemon=True).start()
    return {"status": "started"}


@router.get("/families/refresh")
def refresh_status():
    return family.get_state()


@router.get("/families")
def graph():
    return family.get_graph()


@router.get("/families/path")
def path(from_id: int = Query(..., alias="from"), to_id: int = Query(..., alias="to")):
    chain = family.find_path(from_id, to_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="No path found in current graph")
    return {"chain": chain, "length": max(len(chain) - 1, 0)}


@router.get("/entities/{entity_id}")
def entity_detail(entity_id: int):
    detail = family.get_entity_detail(entity_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return detail
