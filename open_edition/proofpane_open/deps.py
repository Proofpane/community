"""The two access checks every route uses: a signed-in account, and an admin. In their own module so that the ADE's
routes (`ade/routes.py`) and the app (`main.py`) use the same ones."""

from typing import Optional

from fastapi import Depends, Header, HTTPException

from . import auth


def bearer(authorization: Optional[str] = Header(default=None)):
    """The token the request's Authorization header carries ("Bearer TOKEN"), or None: for a route that checks the
    sign-in again inside its own change (auth.decide_device_signin), after current_user has checked it."""
    return authorization[7:] if authorization and authorization.startswith("Bearer ") else None


def current_user(authorization: Optional[str] = Header(default=None)):
    token = bearer(authorization)
    user = auth.token_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    return user


def admin_user(user=Depends(current_user)):
    if not user["admin"]:
        raise HTTPException(status_code=403, detail="Only an admin can do that.")
    return user
