"""目前身分。

email / 姓名來自 IdP token,**不是**從業務庫讀出來的——業務庫沒有這些欄位。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..schemas import MeOut
from ..security import Identity, get_identity

router = APIRouter(prefix="/v1", tags=["me"])


@router.get("/me", response_model=MeOut, summary="目前登入者(含開通狀態)")
async def me(identity: Annotated[Identity, Depends(get_identity)]) -> MeOut:
    # 這支刻意用 get_identity 而非 require_active:待開通的人也要看得到自己的狀態,
    # 否則使用者只會撞到 403 卻不知道自己是誰、該找誰開通。
    return MeOut(
        user_id=identity.user.id,
        sub=identity.user.sub,
        status=identity.user.status,
        platform_role=identity.user.platform_role,
        email=identity.display_email,
        name=identity.display_name,
    )
