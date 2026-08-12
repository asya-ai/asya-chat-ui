from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models import User
from app.services.org_service import require_super_admin
from app.services.system_diagnosis import SystemDiagnosis, diagnose_system

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/system-diagnosis", response_model=SystemDiagnosis)
def system_diagnosis(
    current_user: User = Depends(get_current_user),
) -> SystemDiagnosis:
    require_super_admin(current_user)
    return diagnose_system()
