from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from attendance_teams_bot.observability import install_http_request_observability
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter, TeamsTextReply

_SAFE_MESSAGE_REPLY = "Please send a text message so I can help."


def create_teams_http_app(adapter: TeamsActivityAdapter) -> FastAPI:
    """Create the unauthenticated local-only Teams-compatible HTTP endpoint."""
    app = FastAPI()
    install_http_request_observability(app)

    @app.post("/api/messages")
    async def receive_activity(request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError:
            return _teams_reply_response(TeamsTextReply(text=_SAFE_MESSAGE_REPLY), {})

        if not isinstance(payload, dict):
            return _teams_reply_response(TeamsTextReply(text=_SAFE_MESSAGE_REPLY), {})

        reply = adapter.handle_payload(payload)
        if reply is None:
            return Response(status_code=204)

        return _teams_reply_response(reply, payload)

    return app


def _teams_reply_response(reply: TeamsTextReply, payload: dict[str, Any]) -> JSONResponse:
    content: dict[str, str] = {"type": "message", "text": reply.text}
    activity_id = payload.get("id")
    if isinstance(activity_id, str) and activity_id.strip():
        content["replyToId"] = activity_id.strip()

    return JSONResponse(content=content)
