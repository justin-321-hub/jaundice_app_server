from typing import Optional, Dict, Any
from firebase_admin import auth, credentials
from fastapi import Header, HTTPException, status
from .firebase_config import init_firebase

# 只初始化一次

init_firebase()


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:                   
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )

    return parts[1].strip()


async def verify_firebase_user(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    token = _extract_bearer_token(authorization)

    try:
        decoded = auth.verify_id_token(token)
        return decoded
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"INVALID TOKEN: {e}",
        )