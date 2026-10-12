import pytest

from attendance_teams_bot.agent.mcp_catalog import (
    _ALLOWED_READ_ONLY_TOOL_NAMES,
    DiscoveredMcpTool,
    admit_mcp_catalog,
)
from attendance_teams_bot.mcp.contracts import SELF_ATTENDANCE_TOOL


def compatible_tool(*, description: str = "Remote description") -> DiscoveredMcpTool:
    return DiscoveredMcpTool(
        name=SELF_ATTENDANCE_TOOL,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": ["start_date", "end_date"],
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": True},
    )


def allowed_catalog() -> tuple[DiscoveredMcpTool, ...]:
    return tuple(
        compatible_tool()
        if name == SELF_ATTENDANCE_TOOL
        else DiscoveredMcpTool(
            name=name,
            description="Unprompted legacy read-only tool",
            input_schema={"type": "object"},
            annotations={"readOnlyHint": True},
        )
        for name in sorted(_ALLOWED_READ_ONLY_TOOL_NAMES)
    )


def test_admission_passes_server_metadata_through_for_the_requester_tool() -> None:
    catalog = admit_mcp_catalog(allowed_catalog())

    assert catalog is not None
    definition = catalog.model_tools[0]
    assert definition.description == "Remote description"
    assert definition.input_schema is catalog.policies[0].definition.input_schema
    assert catalog.selected_tool(SELF_ATTENDANCE_TOOL) is not None
    assert catalog.selected_tool("list_employees") is None
    assert definition.input_schema["properties"] == compatible_tool().input_schema["properties"]


@pytest.mark.parametrize(
    "tools",
    [
        (),
        (compatible_tool(), compatible_tool()),
        (DiscoveredMcpTool(SELF_ATTENDANCE_TOOL, "x", {"type": "object"}, {"readOnlyHint": True}),),
        (
            DiscoveredMcpTool(
                SELF_ATTENDANCE_TOOL, "x", {"type": "object"}, {"readOnlyHint": False}
            ),
        ),
        (DiscoveredMcpTool(None, "x", {"type": "object"}, {"readOnlyHint": True}),),
        (
            DiscoveredMcpTool(
                SELF_ATTENDANCE_TOOL,
                "x",
                {"type": "object"},
                {"readOnlyHint": "true"},
            ),
        ),
        (
            DiscoveredMcpTool(
                SELF_ATTENDANCE_TOOL,
                "x",
                {"type": "object", "properties": {"employee_id": {"type": "integer"}}},
                {"readOnlyHint": True},
            ),
        ),
    ],
)
def test_admission_rejects_untrusted_or_incompatible_catalogs(
    tools: tuple[DiscoveredMcpTool, ...],
) -> None:
    assert admit_mcp_catalog(tools) is None


def test_admission_ignores_unknown_discovery_entries_but_retains_allowed_entries() -> None:
    catalog = admit_mcp_catalog(
        (DiscoveredMcpTool("admin_tool", object(), object(), object()), compatible_tool())
    )

    assert catalog is not None
    assert catalog.admitted_tool_names == frozenset({SELF_ATTENDANCE_TOOL})
    assert catalog.model_tools[0].description == "Remote description"


def test_admission_rejects_duplicate_or_invalid_allowed_entries() -> None:
    duplicate = (compatible_tool(), compatible_tool())
    invalid = (
        DiscoveredMcpTool(SELF_ATTENDANCE_TOOL, "x", {"type": "object"}, {"readOnlyHint": True}),
    )

    assert admit_mcp_catalog(duplicate) is None
    assert admit_mcp_catalog(invalid) is None


def test_admission_rejects_oversized_nested_schema() -> None:
    oversized = DiscoveredMcpTool(
        SELF_ATTENDANCE_TOOL,
        "x",
        {"type": "object", "properties": {str(index): index for index in range(257)}},
        {"readOnlyHint": True},
    )

    assert admit_mcp_catalog((oversized,)) is None


def test_current_status_admits_only_category_tool_without_exposing_as_of() -> None:
    catalog = admit_mcp_catalog(
        (
            compatible_tool(),
            DiscoveredMcpTool(
                "get_current_work_status",
                "current",
                {
                    "type": "object",
                    "properties": {
                        "as_of": {"type": "string"},
                        "statuses": {"type": "array"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                },
                {"readOnlyHint": True},
            ),
        )
    )

    assert catalog is not None
    policy = catalog.selected_tool("get_current_work_status")
    assert policy is not None
    assert catalog.selected_tool("get_current_attendance") is None
    assert "as_of" in policy.definition.input_schema["properties"]
    assert policy.validate_arguments({}) == ({}, "en")
    assert policy.validate_arguments({"statuses": ["office", "remote"]}) == (
        {"statuses": ("office", "remote")},
        "en",
    )
    assert policy.validate_arguments({"status": "office", "reply_language": "en"}) is None
