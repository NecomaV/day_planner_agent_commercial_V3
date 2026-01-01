from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_api_key
from app.db import get_db
from app.debug_info import build_db_debug

router = APIRouter(prefix="/debug", tags=["debug"], dependencies=[Depends(require_api_key)])


@router.get("/db")
def debug_db(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return build_db_debug(db, user.id)
