"""Consistent error responses.

Every error has the shape:
    {"error": {"code": "SOME_CODE", "message": "Readable text", "details": ...}}
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.message = message
        self.details = details


class AccountNotFound(AppError):
    status_code = 404
    code = "ACCOUNT_NOT_FOUND"

    def __init__(self, account_id: str):
        super().__init__(f"Account '{account_id}' was not found.", {"account_id": account_id})


class InsufficientBalance(AppError):
    status_code = 422
    code = "INSUFFICIENT_BALANCE"


class DuplicateTransaction(AppError):
    status_code = 409
    code = "DUPLICATE_TRANSACTION"


class SameAccountTransfer(AppError):
    status_code = 400
    code = "SAME_ACCOUNT_TRANSFER"


def error_json(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


# Field name -> specific error code, so clients can tell common problems apart.
_FIELD_CODES = {
    "type": "INVALID_TRANSACTION_TYPE",
    "amount": "INVALID_AMOUNT",
    "page": "INVALID_PAGINATION",
    "limit": "INVALID_PAGINATION",
}


def _validation_code(errors: list[dict]) -> str:
    for err in errors:
        loc = err.get("loc", ())
        if err.get("type") == "json_invalid" or loc == ("body",):
            return "INVALID_REQUEST_BODY"
    for err in errors:
        loc = err.get("loc", ())
        if loc and loc[-1] in _FIELD_CODES:
            return _FIELD_CODES[loc[-1]]
    return "VALIDATION_ERROR"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return error_json(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        details = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")),
                "message": err.get("msg"),
            }
            for err in errors
        ]
        return error_json(400, _validation_code(errors), "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return error_json(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, __: Exception):
        return error_json(500, "INTERNAL_ERROR", "An unexpected error occurred.")
