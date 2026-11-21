import importlib
import pkgutil
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from music_dashboard.presentation.api import routes

app = FastAPI(title="Music Dashboard", version="1.0.0", docs_url=None, redoc_url=None)

# Each module exports router: adding a module never edits this shared registry.
for module in sorted(pkgutil.iter_modules(routes.__path__), key=lambda item: item.name):
    router_module = importlib.import_module(f"{routes.__name__}.{module.name}")
    if hasattr(router_module, "router"):
        app.include_router(router_module.router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Never echo submitted values: validation errors may contain private user input.
    return JSONResponse(
        status_code=400,
        content={
            "code": "invalid_request",
            "message": "Invalid request parameters",
            "details": [".".join(str(v) for v in e["loc"]) + ": " + e["msg"] for e in exc.errors()],
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "invalid_request",
            "message": str(exc.detail),
            "details": [],
        },
    )


_original_openapi = app.openapi


def public_openapi() -> dict[str, Any]:
    schema = _original_openapi()
    for path in schema["paths"].values():
        for operation in path.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)
    # FastAPI's removed 422 response components are not part of the frozen contract.
    for name in ("HTTPValidationError", "ValidationError"):
        schema.get("components", {}).get("schemas", {}).pop(name, None)
    return schema


app.openapi = public_openapi  # type: ignore[method-assign]
