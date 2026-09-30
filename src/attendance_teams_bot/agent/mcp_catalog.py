"""Bot-owned admission of untrusted authenticated MCP tool catalogs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from attendance_teams_bot.agent.language_model import ReplyLanguage, ToolDefinition
from attendance_teams_bot.mcp.contracts import SELF_ATTENDANCE_TOOL, ListMyAttendanceArguments

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ANNOTATION_TYPES: dict[str, type[str] | type[bool]] = {
    "title": str,
    "readOnlyHint": bool,
    "destructiveHint": bool,
    "idempotentHint": bool,
    "openWorldHint": bool,
}
_ALLOWED_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "list_attendance_events",
        "list_my_attendance_events",
        "get_attendance_event",
        "get_daily_attendance",
        "get_planned_work",
        "get_current_attendance",
        "get_employee_attendance_analysis",
        "get_employee_attendance_summary",
        "get_exceptions",
        "get_organization_attendance_analysis",
        "list_employees",
        "get_employee",
        "list_punch_types",
        "list_locations",
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveredMcpTool:
    """Transport-neutral, untrusted MCP discovery fields supplied by an adapter."""

    name: object
    description: object
    input_schema: object
    annotations: object


@dataclass(frozen=True, slots=True)
class AdmittedReadOnlyTool:
    """A bot-owned policy for the one requester-scoped MCP tool the model may select."""

    definition: ToolDefinition

    def validate_arguments(
        self, arguments: Mapping[str, object]
    ) -> tuple[ListMyAttendanceArguments, ReplyLanguage] | None:
        """Accept only a contract-bounded requester range and reply language."""
        if not _has_bounded_arguments(arguments):
            return None
        try:
            language = arguments["reply_language"]
            if language not in {"en", "sl"}:
                return None
            return (
                ListMyAttendanceArguments(
                    start_date=date.fromisoformat(str(arguments["start_date"])),
                    end_date=date.fromisoformat(str(arguments["end_date"])),
                ),
                language,
            )
        except KeyError, ValueError:
            return None


@dataclass(frozen=True, slots=True)
class DisabledReferenceToolPolicy:
    """Record a deliberately non-executable reference-tool policy boundary."""

    name: str
    dependency: str
    enabled: bool = False


REFERENCE_TOOL_POLICY_PLACEHOLDERS = (
    DisabledReferenceToolPolicy(
        "list_punch_types", "authoritative versioned result schema and localized typed renderer"
    ),
    DisabledReferenceToolPolicy(
        "list_locations", "authoritative versioned result schema and localized typed renderer"
    ),
)


@dataclass(frozen=True, slots=True)
class AdmittedMcpCatalog:
    """The small catalog interface exposed to orchestration after admission succeeds."""

    admitted_tool_names: frozenset[str]
    requester_attendance_tool: AdmittedReadOnlyTool

    @property
    def model_tools(self) -> tuple[ToolDefinition, ...]:
        return (self.requester_attendance_tool.definition,)

    def selected_tool(self, name: str) -> AdmittedReadOnlyTool | None:
        """Return the admitted policy only when ``name`` is the canonical tool name."""
        if name == self.requester_attendance_tool.definition.name:
            return self.requester_attendance_tool
        return None


def admit_mcp_catalog(
    discovered_tools: Sequence[DiscoveredMcpTool],
) -> AdmittedMcpCatalog | None:
    """Admit allowed discovery, while exposing only locally enabled executable policies.

    Discovery is an untrusted capability inventory. Unknown advertised tools are
    intentionally ignored; allowed entries must still be well-formed and unique.
    """
    remote_tools: list[_ValidatedMcpTool] = []
    for discovered in discovered_tools:
        if (
            not isinstance(discovered.name, str)
            or discovered.name not in _ALLOWED_READ_ONLY_TOOL_NAMES
        ):
            continue
        remote = _validate_discovered_tool(discovered)
        if remote is None:
            return None
        remote_tools.append(remote)
    remote_by_name = {tool.name: tool for tool in remote_tools}
    if len(remote_by_name) != len(remote_tools):
        return None
    if not remote_by_name:
        return None
    if not all(tool.annotations.get("readOnlyHint") is True for tool in remote_tools):
        return None
    selected = remote_by_name.get(SELF_ATTENDANCE_TOOL)
    if selected is None or not _is_compatible_self_attendance_schema(selected.input_schema):
        return None
    return AdmittedMcpCatalog(
        frozenset(remote_by_name), AdmittedReadOnlyTool(canonical_self_attendance_tool())
    )


def canonical_self_attendance_tool() -> ToolDefinition:
    """Return the bot-owned prompt schema; remote MCP metadata is never prompted."""
    return ToolDefinition(
        name=SELF_ATTENDANCE_TOOL,
        description=(
            "List the authenticated requester's attendance events for an inclusive date range of "
            "no more than 31 calendar days."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "reply_language": {"type": "string", "enum": ["en", "sl"]},
            },
            "required": ["start_date", "end_date", "reply_language"],
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": True},
    )


@dataclass(frozen=True, slots=True)
class _ValidatedMcpTool:
    name: str
    input_schema: Mapping[str, object]
    annotations: Mapping[str, object]


def _validate_discovered_tool(discovered: DiscoveredMcpTool) -> _ValidatedMcpTool | None:
    if (
        not isinstance(discovered.name, str)
        or _TOOL_NAME.fullmatch(discovered.name) is None
        or not isinstance(discovered.description, str)
        or len(discovered.description) > 4_096
        or not isinstance(discovered.input_schema, Mapping)
        or not _is_safe_json_value(discovered.input_schema)
    ):
        return None
    annotations = _normalize_annotations(discovered.annotations)
    if annotations is None:
        return None
    return _ValidatedMcpTool(discovered.name, discovered.input_schema, annotations)


def _normalize_annotations(annotations: object) -> Mapping[str, object] | None:
    if annotations is None:
        return {}
    if not isinstance(annotations, Mapping):
        return None
    normalized: dict[str, object] = {}
    for key, value in annotations.items():
        if not isinstance(key, str) or key not in _ANNOTATION_TYPES:
            return None
        if type(value) is not _ANNOTATION_TYPES[key]:
            return None
        normalized[key] = value
    return normalized


def _is_safe_json_value(value: object, depth: int = 0) -> bool:
    if depth > 16:
        return False
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return len(value) <= 4_096
    if isinstance(value, Mapping):
        return len(value) <= 256 and all(
            isinstance(key, str) and len(key) <= 256 and _is_safe_json_value(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return len(value) <= 256 and all(_is_safe_json_value(item, depth + 1) for item in value)
    return False


def _is_compatible_self_attendance_schema(schema: Mapping[str, object]) -> bool:
    if schema.get("type") != "object":
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        return False
    expected_types = {
        "start_date": "string",
        "end_date": "string",
        "limit": "integer",
        "offset": "integer",
    }
    if any(name not in properties for name in expected_types):
        return False
    if any(name not in expected_types for name in required):
        return False
    for name, expected_type in expected_types.items():
        definition = properties[name]
        if not isinstance(definition, Mapping) or definition.get("type") != expected_type:
            return False
    forbidden = {"employee_id", "email", "role", "actor_id", "tenant_id", "object_id"}
    return not forbidden.intersection(properties)


def _has_bounded_arguments(arguments: Mapping[str, object]) -> bool:
    """Keep unusably large model output out of validation and downstream calls."""
    return (
        len(arguments) == 3
        and set(arguments) == {"start_date", "end_date", "reply_language"}
        and all(
            isinstance(key, str) and len(key) <= 64 and isinstance(value, str) and len(value) <= 32
            for key, value in arguments.items()
        )
    )
