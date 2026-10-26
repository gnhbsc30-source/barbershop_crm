from fastapi import FastAPI

from app.api.auth import AUTH_EXCEPTION_HANDLERS, router as auth_router
from app.api.auth_dependencies import authentication_lifespan
from app.api.customer_booking import router as customer_router
from app.core.local_security import LoopbackSecurityMiddleware


app = FastAPI(
    title="Barbershop CRM",
    version="0.1.0",
    lifespan=authentication_lifespan,
    exception_handlers=AUTH_EXCEPTION_HANDLERS,
    redirect_slashes=False,
    docs_url=None, redoc_url=None, openapi_url=None,
)
app.add_middleware(LoopbackSecurityMiddleware)
app.include_router(auth_router)
app.include_router(customer_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
