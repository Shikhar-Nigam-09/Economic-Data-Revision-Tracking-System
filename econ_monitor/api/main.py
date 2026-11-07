"""FastAPI application factory, lifespan handler, CORS, error handling, and core endpoints."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from econ_monitor.api.routers.analytics import router as analytics_router
from econ_monitor.api.routers.anomalies import router as anomalies_router
from econ_monitor.api.routers.ingestion import router as ingestion_router
from econ_monitor.api.routers.notifications import router as notifications_router
from econ_monitor.api.routers.observations import router as observations_router
from econ_monitor.api.routers.quality import router as quality_router
from econ_monitor.api.routers.revisions import router as revisions_router
from econ_monitor.api.routers.series import router as series_router
from econ_monitor.api.routers.subscribers import router as subscribers_router
from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.errors import ConfigurationError, EconMonitorError, SeriesNotFoundError
from econ_monitor.core.logging import get_logger, setup_logging
from econ_monitor.db.session import get_engine
from econ_monitor.schemas.common import ErrorDetail, ErrorResponse
from econ_monitor.schemas.health import HealthResponse

logger = get_logger("econ_monitor.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle context manager."""
    setup_logging()
    logger.info("Initializing Economic Data Monitoring API")
    yield
    logger.info("Shutting down Economic Data Monitoring API")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="Economic Data Monitoring and Revision Tracking System",
        description="Tracks macroeconomic time series, revisions, anomalies, and quality metrics.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS configuration restricted to local origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://127.0.0.1",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:8501",
            "http://127.0.0.1:8501",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --------------------------------------------------------------------------
    # Custom Exception Handlers (Format: {error: {code, message, details}})
    # --------------------------------------------------------------------------

    @app.exception_handler(SeriesNotFoundError)
    async def series_not_found_handler(request: Request, exc: SeriesNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details)
            ).model_dump(),
        )

    @app.exception_handler(ConfigurationError)
    async def config_error_handler(request: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details)
            ).model_dump(),
        )

    @app.exception_handler(EconMonitorError)
    async def econ_monitor_error_handler(request: Request, exc: EconMonitorError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details)
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="Request validation failed.",
                    details={"errors": exc.errors()},
                )
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="HTTP_ERROR",
                    message=str(exc.detail),
                    details={},
                )
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(f"Unhandled server error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INTERNAL_SERVER_ERROR",
                    message="An unexpected server error occurred.",
                    details={"error": str(exc)},
                )
            ).model_dump(),
        )

    # --------------------------------------------------------------------------
    # Health Endpoint
    # --------------------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    def health_check(response: Response) -> HealthResponse:
        """Check application status and database connectivity. Returns HTTP 503 if DB is down."""
        now = get_utc_now()
        db_status = "disconnected"
        is_healthy = False

        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1;"))
            db_status = "connected"
            is_healthy = True
        except Exception:
            db_status = "disconnected"
            is_healthy = False

        if not is_healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(
                status="degraded",
                database=db_status,
                version="0.1.0",
                timestamp=now,
            )

        return HealthResponse(
            status="ok",
            database=db_status,
            version="0.1.0",
            timestamp=now,
        )

    # Mount API v1 Routers
    app.include_router(series_router, prefix="/api/v1")
    app.include_router(observations_router, prefix="/api/v1")
    app.include_router(revisions_router, prefix="/api/v1")
    app.include_router(analytics_router, prefix="/api/v1")
    app.include_router(anomalies_router, prefix="/api/v1")
    app.include_router(quality_router, prefix="/api/v1")
    app.include_router(subscribers_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(ingestion_router, prefix="/api/v1")

    return app


app = create_app()
