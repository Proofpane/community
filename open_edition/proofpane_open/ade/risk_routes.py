"""The route of the terms at each risk point: `router`, under /api/ade/risks, which main.py includes.

- GET /api/ade/risks, Depends(current_user): {"texts": risk_texts.texts()}, every risk point's text, the operator
  (OPEN_EDITION_OPERATOR, read by risk_texts.operator()) and the record's keeping (tasks.record_days()) filled in;
  RiskTextsView in ui/src/api.ts.

It serves the texts and nothing else: nothing is acknowledged or kept of a pop-up, and no route knows of one, so a call
to the API is never held by one. The teammate track's routes (ade/task_offers.py) return point 13's text themselves,
from risk_texts.text(13), with an offer and a custody request."""

from fastapi import APIRouter, Depends

from ..deps import current_user
from . import risk_texts

router = APIRouter(prefix="/api/ade/risks")


@router.get("")
def risks(user=Depends(current_user)):
    return {"texts": risk_texts.texts()}
