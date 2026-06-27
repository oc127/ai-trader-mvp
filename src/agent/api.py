"""FastAPI backend for the AI Trading Agent web interface."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent.core import TradingAgent
from src.agent.memory import Memory
from src.agent.tools import TradingTools
from src.hl_client.rest import HLRestClient

load_dotenv()

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

app = FastAPI(title="AI Trading Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sessions: dict[str, TradingAgent] = {}


def _get_agent(session_id: str) -> TradingAgent:
    if session_id not in _sessions:
        client = HLRestClient({"exchange": {"use_testnet": False}})
        tools = TradingTools(client, paper=True)
        memory = Memory(f"data/memory_{session_id}.json")
        _sessions[session_id] = TradingAgent(tools, memory)
    return _sessions[session_id]


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    agent = _get_agent(req.session_id)
    reply = agent.chat(req.message)
    return ChatResponse(reply=reply)


@app.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    agent = _get_agent(session_id)

    try:
        while True:
            message = await websocket.receive_text()
            reply = agent.chat(message)
            await websocket.send_json({"type": "reply", "content": reply})
    except WebSocketDisconnect:
        pass


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "sessions": len(_sessions)}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
