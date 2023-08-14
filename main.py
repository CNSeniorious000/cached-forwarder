from os import getenv
from time import time
from traceback import format_exc
from urllib.parse import urljoin

from brotli_asgi import BrotliMiddleware
from diskcache import Cache
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from httpx import AsyncClient

load_dotenv()

baseurl = getenv("BASEURL")
min_age = eval(getenv("MIN_AGE", "3600"))
excluded_headers = {
    "content-encoding",
    "content-length",
    "content-security-policy",
    "connection",
} | set(getenv("EXCLUDED_HEADERS", "").split())

client = AsyncClient(http2=True, base_url=baseurl)
cache = Cache(".cache", eviction_policy="none", statics=True)
app = FastAPI(openapi_url=None)
app.add_middleware(BrotliMiddleware)
app.add_middleware(CORSMiddleware, allow_origins="*", max_age=min_age or None)


@app.get("/{path:path}")
async def handle_get_request(path: str | None = ""):
    cache_key = (baseurl, path)
    hit = cache.get(cache_key)

    hits, misses = cache.stats()
    common_headers = {"x-diskcache-hits": str(hits), "x-diskcache-misses": str(misses)}

    if not hit or min_age and (age := time() - hit["timestamp"]) > min_age:
        url = urljoin(baseurl, path)

        print(f"\n < fetch {url!r}")

        res = await client.get(url)

        res_headers = res.headers.copy()
        res_body = res.read()
        res_status = res.status_code

        for h in excluded_headers:
            res_headers.pop(h, None)

        print(f"\n > {res_status} | {len(res_body)} bytes\n")
        for k, v in res_headers.items():
            print(f"{k:>20}: {v}")
        print()

        if "location" in res_headers:
            res_headers["location"] = res_headers["location"].replace(baseurl, "")

        if res_status < 400 or res_status:
            cache.set(
                cache_key,
                {
                    "body": res_body,
                    "headers": dict(res_headers),
                    "status": res_status,
                    "timestamp": time(),
                },
            )
            age = 0
        elif hit:
            res_body = hit["body"]
            res_status = hit["status"]
            res_headers = hit["headers"]
        else:
            age = 0

        common_headers["x-diskcache-age"] = f"{age:.0f}"

        return Response(res_body, res_status, common_headers | dict(res_headers))

    common_headers["x-diskcache-age"] = f"{time() - hit['timestamp']:.0f}"

    return Response(hit["body"], hit["status"], common_headers | hit["headers"])


@app.head("/{path:path}")
async def handle_head_request(path: str | None = ""):
    res: Response = await handle_get_request(path)
    res.body = None
    return res


@app.exception_handler(Exception)
async def handle_exception(*_):
    return Response(format_exc(), 500, media_type="text/plain")
