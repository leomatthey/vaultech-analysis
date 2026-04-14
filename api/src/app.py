"""FastAPI application for forging line delay diagnostics.

Loads reference_times.json once at startup via the lifespan context manager.
The POST /diagnose route delegates all logic to the pure diagnose() function.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.diagnose import CAUSE_TABLE, diagnose

REFERENCE_TIMES_PATH = Path(__file__).resolve().parent.parent / "reference_times.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load reference times once at startup."""
    with open(REFERENCE_TIMES_PATH) as f:
        app.state.reference_times = json.load(f)
    app.state.cause_table = CAUSE_TABLE
    yield


app = FastAPI(title="Forging Line Diagnostics API", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 400 with simplified error message instead of FastAPI's default 422."""
    return JSONResponse(
        status_code=400,
        content={"error": str(exc.errors()[0]["msg"])},
    )


class DiagnoseRequest(BaseModel):
    piece_id: str
    die_matrix: int
    lifetime_2nd_strike_s: float | None = None
    lifetime_3rd_strike_s: float | None = None
    lifetime_4th_strike_s: float | None = None
    lifetime_auxiliary_press_s: float | None = None
    lifetime_bath_s: float | None = None


@app.post("/diagnose")
async def diagnose_piece(body: DiagnoseRequest):
    """Receive timing data for one piece and return delay diagnosis."""
    reference_times = app.state.reference_times
    cause_table = app.state.cause_table

    if str(body.die_matrix) not in reference_times:
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown die_matrix {body.die_matrix}"},
        )

    piece = body.model_dump()
    return diagnose(piece, reference_times, cause_table)
