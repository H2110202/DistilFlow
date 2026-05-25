import json
import time
import httpx
from typing import Optional
from backend.config import get_settings

_access_token_cache: dict = {}


def get_access_token() -> str:
    settings = get_settings()
    app_key = settings.dingtalk_app_key
    app_secret = settings.dingtalk_app_secret
    if not app_key or not app_secret:
        return ""

    cache_key = f"token_{app_key}"
    cached = _access_token_cache.get(cache_key)
    if cached and cached.get("expires_at", 0) > time.time() + 60:
        return cached["token"]

    try:
        resp = httpx.post(
            "https://oapi.dingtalk.com/gettoken",
            params={"appkey": app_key, "appsecret": app_secret},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            token = data["access_token"]
            _access_token_cache[cache_key] = {
                "token": token,
                "expires_at": time.time() + data.get("expires_in", 7200),
            }
            return token
    except Exception:
        pass
    return ""


def get_user_token(auth_code: str) -> Optional[dict]:
    settings = get_settings()
    app_key = settings.dingtalk_app_key
    app_secret = settings.dingtalk_app_secret
    if not app_key or not app_secret:
        return None

    try:
        resp = httpx.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": app_key,
                "clientSecret": app_secret,
                "code": auth_code,
                "grantType": "authorization_code",
            },
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("accessToken"):
            return {
                "access_token": data["accessToken"],
                "refresh_token": data.get("refreshToken", ""),
                "expire_in": data.get("expireIn", 7200),
            }
    except Exception:
        pass
    return None


def get_user_info(user_access_token: str) -> Optional[dict]:
    try:
        resp = httpx.get(
            "https://api.dingtalk.com/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": user_access_token},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("openId"):
            return {
                "open_id": data.get("openId", ""),
                "union_id": data.get("unionId", ""),
                "name": data.get("nick", data.get("name", "")),
                "avatar": data.get("avatarUrl", ""),
                "mobile": data.get("mobile", ""),
                "email": data.get("email", ""),
                "corp_id": data.get("corpId", ""),
            }
    except Exception:
        pass
    return None


def get_user_detail_by_userid(userid: str) -> Optional[dict]:
    access_token = get_access_token()
    if not access_token or not userid:
        return None

    try:
        resp = httpx.get(
            f"https://oapi.dingtalk.com/topapi/v2/user/get",
            params={"access_token": access_token},
            json={"userid": userid},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            result = data.get("result", {})
            return {
                "name": result.get("name", ""),
                "dept_id": result.get("dept_id_list", []),
                "title": result.get("title", ""),
                "mobile": result.get("mobile", ""),
                "email": result.get("email", ""),
                "avatar": result.get("avatar", ""),
            }
    except Exception:
        pass
    return None


def get_userid_by_unionid(union_id: str) -> Optional[str]:
    access_token = get_access_token()
    if not access_token or not union_id:
        return None

    try:
        resp = httpx.post(
            "https://oapi.dingtalk.com/topapi/user/getbyunionid",
            params={"access_token": access_token},
            json={"unionid": union_id},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            return data.get("result", {}).get("userid")
    except Exception:
        pass
    return None
