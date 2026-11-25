"""Medicine search, confirmation, detail, user list, and interaction-check endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.safety import MEDICAL_DISCLAIMER
from app.dependencies import ServiceContainer, get_container
from app.schemas.medicine import (
    CheckRequest,
    CheckResponse,
    ConfirmedMedicineEntry,
    ConfirmMedicineRequest,
    MedicineDetailResponse,
    MedicineSearchResponse,
)
from app.services.interaction_checker import ConfirmationRequiredError

router = APIRouter(prefix="/api/v1", tags=["Medicines & Interactions"])


@router.get("/medicines/search", response_model=MedicineSearchResponse)
async def search_medicines(
    q: str = Query(..., min_length=1, max_length=500, description="Medicine name or raw OCR strip text."),
    is_ocr: bool = Query(default=False, description="Enable OCR packaging noise cleanup before searching."),
    limit: int = Query(default=10, ge=1, le=25, description="Maximum candidate results to return."),
    container: ServiceContainer = Depends(get_container),
) -> MedicineSearchResponse:
    """Search real medicine sources with OCR-compatible text normalization."""
    result = await container.search_service.search(query=q, is_ocr=is_ocr, limit=limit)
    return MedicineSearchResponse(**result)


@router.post(
    "/medicines/confirm",
    response_model=ConfirmedMedicineEntry,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_medicine(
    payload: ConfirmMedicineRequest,
    container: ServiceContainer = Depends(get_container),
) -> ConfirmedMedicineEntry:
    """Explicitly confirm a searched medicine and add it to the user's medicine list."""
    try:
        entry = await container.search_service.confirm_and_add(
            medicine_id=payload.medicine_id,
            confirmed=payload.confirmed,
            user_dosage_note=payload.user_dosage_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConfirmedMedicineEntry(**entry)


@router.get("/medicines/{medicine_id}", response_model=MedicineDetailResponse)
async def get_medicine_detail(
    medicine_id: str,
    container: ServiceContainer = Depends(get_container),
) -> MedicineDetailResponse:
    """Retrieve verified composition, RxNorm properties, and openFDA label info for a medicine."""
    try:
        detail = await container.detail_service.get_detail(medicine_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return MedicineDetailResponse(**detail)


@router.get("/medicine-list")
async def list_confirmed_medicines(
    container: ServiceContainer = Depends(get_container),
) -> dict[str, object]:
    """Return all medicines confirmed by the user."""
    items = container.cache.list_confirmed_medicines()
    return {
        "total": len(items),
        "items": items,
        "disclaimer": MEDICAL_DISCLAIMER,
    }


@router.delete("/medicine-list/{medicine_id}", status_code=status.HTTP_200_OK)
async def remove_confirmed_medicine(
    medicine_id: str,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, object]:
    """Remove a medicine from the user's confirmed list."""
    deleted = container.cache.remove_confirmed_medicine(medicine_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medicine '{medicine_id}' was not found in the user's confirmed list.",
        )
    return {"deleted": True, "id": medicine_id}


@router.post("/check", response_model=CheckResponse)
async def check_interactions(
    payload: CheckRequest,
    container: ServiceContainer = Depends(get_container),
) -> CheckResponse:
    """Check confirmed medicines for duplicate active ingredients and verified interactions.

    Returns grey 'Could not check' whenever a verified, licensed interaction source is unavailable.
    Never claims a medicine or combination is safe.
    """
    medicines_dicts = (
        [item.model_dump() for item in payload.medicines]
        if payload.medicines is not None
        else None
    )
    try:
        result = await container.checker_service.check(
            medicines=medicines_dicts,
            use_saved_list=payload.use_saved_list,
        )
    except ConfirmationRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return CheckResponse(**result)
