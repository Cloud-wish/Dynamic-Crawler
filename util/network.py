from __future__ import annotations
import asyncio
import httpx
from http.cookiejar import CookieJar
from functools import partial
from typing import Any

def cookie_str_to_dict(cookie_str: str):
    cookies_list = cookie_str.split(";")
    if len(cookies_list) != 0 and len(cookies_list[-1]) == 0:
        cookies_list.pop()
    cookies = dict()
    for c in cookies_list:
        cookie_pair = c.lstrip().rstrip().split("=")
        cookies[cookie_pair[0]] = cookie_pair[1]
    return cookies

def cookiejar_to_dict(cookiejar: CookieJar):
    cookie_dict = dict()
    for cookie in cookiejar:
        cookie_name = cookie.name
        if (not cookie_name in cookie_dict) or (len(cookie.domain) != 0):
            cookie_dict[cookie_name] = cookie.value
    return cookie_dict

class Network:
    def __init__(self, cookie_str: str = None, ua_str: str = None) -> None:
        self._client: httpx.AsyncClient = httpx.AsyncClient()
        if not cookie_str is None:
            self.set_cookie(cookie_str)
        if not ua_str is None:
            self.set_ua(ua_str)
        pass
        self._save_cookie_func = None

    def __del__(self):
        # Close client when this object is destroyed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._client.aclose())
            else:
                loop.run_until_complete(self._client.aclose())
        except Exception:
            pass

    def set_cookie(self, cookie_str: str):
        self._client.cookies = httpx.Cookies(cookie_str_to_dict(cookie_str))

    def set_ua(self, ua_str: str):
        self._client.headers.update({"User-Agent": ua_str})

    def set_header(self, header: dict):
        self._client.headers.update(header)

    def get_cookiejar(self) -> CookieJar:
        return self._client.cookies.jar

    def get_ua(self) -> str:
        return str(self._client.headers["User-Agent"])
    
    def set_save_cookie_func(self, func: partial):
        self._save_cookie_func = func

    async def get(self, url: str, headers: dict[str,str] = None, params: dict[str,Any] = None, timeout: int = 30) -> httpx.Response:
        resp = await self._client.get(
            url=url,
            headers=headers,
            params=params,
            timeout=timeout
            )
        if not self._save_cookie_func is None:
            self._save_cookie_func(self.get_cookiejar())
        return resp
    
    async def post(self, url: str, headers: dict[str,str] = None, params: dict[str,Any] = None, timeout: int = 30) -> httpx.Response:
        resp = await self._client.post(
            url=url,
            headers=headers,
            params=params,
            timeout=timeout
            )
        if not self._save_cookie_func is None:
            self._save_cookie_func(self.get_cookiejar())
        return resp