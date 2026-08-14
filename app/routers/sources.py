from fastapi import APIRouter

from app.catalog.ref_sites import catalog_payload
from app.schemas import RefSiteCatalogOut

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/catalog", response_model=RefSiteCatalogOut)
def get_ref_site_catalog() -> RefSiteCatalogOut:
    """UI catalog (single source of truth). Gathering uses the same site ids."""
    return RefSiteCatalogOut.model_validate(catalog_payload())
