import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.routers import adaptation
from app.workers.runner import worker_loop

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop))
    yield
    stop.set()
    await worker
    await db.close_pool()


app = FastAPI(title="Course Engine", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(adaptation.router)


@app.get("/health")
async def health():
    return {"ok": True}
