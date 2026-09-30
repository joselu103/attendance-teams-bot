import pytest

from attendance_teams_bot.agent.mcp_catalog import (
    _ALLOWED_READ_ONLY_TOOL_NAMES,
    DiscoveredMcpTool,
    admit_mcp_catalog,
    canonical_self_attendance_tool,
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


def test_admission_exposes_only_the_canonical_requester_tool() -> None:
    catalog = admit_mcp_catalog(allowed_catalog())

    assert catalog is not None
    assert catalog.model_tools == (canonical_self_attendance_tool(),)
    assert catalog.selected_tool(SELF_ATTENDANCE_TOOL) is not None
    assert catalog.selected_tool("list_employees") is None
    assert "Remote description" not in repr(catalog.model_tools)


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
    assert catalog.model_tools == (canonical_self_attendance_tool(),)


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
