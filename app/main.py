from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.database import initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Barbershop CRM",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    return {"status": "ok"}