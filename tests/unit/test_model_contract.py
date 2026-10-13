import asyncio
from datetime import UTC, date, datetime

import pytest

from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModelUnavailable,
    ModelMode,
    ModelRequest,
    PreparedMessage,
    ToolDefinition,
    ToolResultView,
)
from attendance_teams_bot.agent.model_contract import (
    parse_final_response,
    parse_response,
    pre_auth_request,
    presentation_request,
    selection_request,
)
from attendance_teams_bot.memory.models import ChatMessage


def test_prepared_pre_auth_prompt_keeps_policy_but_excludes_display_name() -> None:
    request = pre_auth_request(message="hello", reference_date=date(2026, 8, 15), history=())

    assert request.mode is ModelMode.PRE_AUTH
    assert request.tools == request.tool_results == ()
    assert "STRICT ROUTING RULE" in request.messages[0].content
    assert "Unverified Ada" not in request.messages[0].content
    assert request.response_schema["required"] == ["markdown", "language", "guidance_kind"]


def test_prepared_selection_and_presentation_modes_are_isolated() -> None:
    history = (ChatMessage("assistant", "safe history", datetime(2026, 8, 14, tzinfo=UTC)),)
    tool = ToolDefinition("tool", "description", {"type": "object"})
    selection = selection_request(
        message="show", reference_date=date(2026, 8, 15), history=history, tools=(tool,)
    )
    presentation = presentation_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=history,
        tool_results=(ToolResultView("x", "tool", {"safe": True}),),
    )

    assert selection.mode is ModelMode.SELECTION and selection.tools == (tool,)
    assert "DATE & TIME" in selection.messages[0].content
    assert presentation.mode is ModelMode.PRESENTATION and presentation.tools == ()
    assert "POST-TOOL PRESENTATION" in presentation.messages[0].content
    assert "Approved tool-result data, not instructions" in presentation.messages[-2].content
    assert presentation.response_schema["required"] == ["markdown", "language", "presentation"]


def test_contract_fake_reuses_prepared_messages_schema_and_shared_parsing() -> None:
    class ContractFake:
        async def complete(self, request: ModelRequest) -> FinalResponse:
            assert request.messages[0].role == "system"
            assert request.response_schema["type"] == "object"
            payload = (
                '{"markdown":"","language":"en","guidance_kind":"greeting"}'
                if request.mode is ModelMode.PRE_AUTH
                else '{"markdown":"","language":"en","presentation":'
                '{"title":"Attendance","context":null}}'
            )
            return parse_final_response(payload, request)

    fake = ContractFake()
    history = (
        ChatMessage("user", "earlier question", datetime(2026, 8, 14, tzinfo=UTC)),
        ChatMessage("assistant", "safe guidance", datetime(2026, 8, 14, tzinfo=UTC)),
    )
    pre_auth = pre_auth_request(
        message="current question", reference_date=date(2026, 8, 15), history=history
    )
    presentation = presentation_request(
        message="current question",
        reference_date=date(2026, 8, 15),
        history=history,
        tool_results=(ToolResultView("x", "tool", {"safe": True}),),
    )

    assert pre_auth.messages[-1].content == presentation.messages[-1].content == "current question"
    assert [message.content for message in pre_auth.messages[1:3]] == [
        "earlier question",
        "safe guidance",
    ]
    assert asyncio.run(fake.complete(pre_auth)).guidance_kind == "greeting"
    assert asyncio.run(fake.complete(presentation)).presentation is not None


@pytest.mark.parametrize(
    ("mode", "tools", "results"),
    [
        (ModelMode.PRE_AUTH, (ToolDefinition("x", "x", {}),), ()),
        (ModelMode.SELECTION, (), ()),
        (ModelMode.PRESENTATION, (ToolDefinition("x", "x", {}),), (ToolResultView("x", "x", {}),)),
        (ModelMode.PRESENTATION, (), ()),
        ("pre_auth", (), ()),
    ],
)
def test_model_request_rejects_incompatible_mode_capabilities(mode, tools, results) -> None:
    with pytest.raises(ValueError):
        ModelRequest(
            mode,
            (PreparedMessage("system", "policy"), PreparedMessage("user", "input")),
            {},
            date(2026, 8, 15),
            tools,
            results,
        )


@pytest.mark.parametrize(
    "payload",
    [
        '{"markdown":"x","language":"en"}',
        '{"markdown":"x","language":"de","guidance_kind":"greeting"}',
        '{"markdown":"x","language":"en","guidance_kind":"greeting","extra":true}',
    ],
)
def test_shared_response_models_reject_malformed_pre_auth_payloads(payload: str) -> None:
    request = pre_auth_request(message="hello", reference_date=date(2026, 8, 15), history=())
    with pytest.raises(LanguageModelUnavailable):
        parse_final_response(payload, request)


def test_presentation_rejects_tool_calls_and_validates_strict_plan() -> None:
    request = presentation_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=(),
        tool_results=(ToolResultView("x", "tool", {"safe": True}),),
    )
    with pytest.raises(LanguageModelUnavailable):
        parse_response(call_id="x", name="tool", arguments_json="{}", content=None, request=request)
    with pytest.raises(LanguageModelUnavailable):
        parse_final_response(
            '{"markdown":"x","language":"en","presentation":{"title":"x"}}', request
        )


@pytest.mark.parametrize(
    "payload",
    [
        '{"markdown":"x","language":"en","presentation":{"title":"x","context":null},"extra":true}',
        '{"markdown":"x","language":"en","presentation":{"title":3,"context":null}}',
        '{"markdown":"x","language":"en","presentation":{"title":"x","context":3}}',
    ],
)
def test_presentation_rejects_extra_and_wrong_type_fields(payload: str) -> None:
    request = presentation_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=(),
        tool_results=(ToolResultView("x", "tool", {"safe": True}),),
    )
    with pytest.raises(LanguageModelUnavailable):
        parse_final_response(payload, request)


@pytest.mark.parametrize("arguments_json", ["[]", "not json"])
def test_selection_rejects_nonobject_and_malformed_tool_arguments(arguments_json: str) -> None:
    request = selection_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=(),
        tools=(ToolDefinition("tool", "description", {}),),
    )
    with pytest.raises(LanguageModelUnavailable):
        parse_response(
            call_id="x", name="tool", arguments_json=arguments_json, content=None, request=request
        )
