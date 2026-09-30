"""Bot-owned admission and execution policy for the authenticated MCP catalog."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from attendance_teams_bot.agent.language_model import ReplyLanguage, ToolDefinition
from attendance_teams_bot.mcp.contracts import (
    CURRENT_ATTENDANCE_TOOL,
    OTHER_ATTENDANCE_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SELF_ATTENDANCE_TOOL,
    ListMyAttendanceArguments,
)

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SELECTOR = re.compile(r"^[^\s<>`]{1,254}$")
_STATUS = frozenset({"office", "remote", "customer_site", "break", "absence", "no_status"})
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
        "resolve_employee",
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveredMcpTool:
    name: object
    description: object
    input_schema: object
    annotations: object


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    definition: ToolDefinition
    kind: Literal["self", "other", "current"]

    def validate_arguments(
        self, arguments: Mapping[str, object]
    ) -> tuple[dict[str, object], ReplyLanguage] | None:
        if not all(
            isinstance(key, str) and isinstance(value, str) and len(value) <= 254
            for key, value in arguments.items()
        ):
            return None
        values = {key: value for key, value in arguments.items() if isinstance(value, str)}
        raw_language = values.get("reply_language")
        if raw_language == "en":
            language: ReplyLanguage = "en"
        elif raw_language == "sl":
            language = "sl"
        else:
            return None
        try:
            if self.kind == "self":
                if set(arguments) != {"start_date", "end_date", "reply_language"}:
                    return None
                bounded = _bounded_dates(values)
                return (
                    {
                        "start_date": bounded.start_date.isoformat(),
                        "end_date": bounded.end_date.isoformat(),
                    },
                    language,
                )
            if self.kind == "other":
                required = {"start_date", "end_date", "reply_language"}
                selectors = {
                    key: values[key]
                    for key in ("employee_id", "username", "email")
                    if key in arguments
                }
                if set(arguments) != required | set(selectors) or len(selectors) != 1:
                    return None
                bounded = _bounded_dates(values)
                selector_name, selector_value = next(iter(selectors.items()))
                if selector_name == "employee_id":
                    if not selector_value.isdecimal() or int(selector_value) < 1:
                        return None
                    selector: object = int(selector_value)
                elif not _SELECTOR.fullmatch(selector_value):
                    return None
                else:
                    selector = selector_value
                return (
                    {
                        "start_date": bounded.start_date.isoformat(),
                        "end_date": bounded.end_date.isoformat(),
                        selector_name: selector,
                    },
                    language,
                )
            if set(arguments) != {"status", "reply_language"} or values["status"] not in _STATUS:
                return None
            return ({"status": values["status"]}, language)
        except KeyError, ValueError:
            return None


@dataclass(frozen=True, slots=True)
class AdmittedMcpCatalog:
    admitted_tool_names: frozenset[str]
    policies: tuple[ToolPolicy, ...]

    @property
    def model_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(policy.definition for policy in self.policies)

    def selected_tool(self, name: str) -> ToolPolicy | None:
        return next((policy for policy in self.policies if policy.definition.name == name), None)


def admit_mcp_catalog(discovered_tools: Sequence[DiscoveredMcpTool]) -> AdmittedMcpCatalog | None:
    remote: dict[str, Mapping[str, object]] = {}
    for tool in discovered_tools:
        if not isinstance(tool.name, str) or tool.name not in _ALLOWED_READ_ONLY_TOOL_NAMES:
            continue
        if tool.name in remote or not _valid_tool(tool):
            return None
        remote[tool.name] = tool.input_schema  # type: ignore[assignment]
    if not _compatible_self(remote.get(SELF_ATTENDANCE_TOOL)):
        return None
    policies = [ToolPolicy(canonical_self_attendance_tool(), "self")]
    if _compatible_resolver(remote.get(RESOLVE_EMPLOYEE_TOOL)) and _compatible_other(
        remote.get(OTHER_ATTENDANCE_TOOL)
    ):
        policies.append(ToolPolicy(canonical_other_attendance_tool(), "other"))
    if _compatible_current(remote.get(CURRENT_ATTENDANCE_TOOL)):
        policies.append(ToolPolicy(canonical_current_attendance_tool(), "current"))
    return AdmittedMcpCatalog(frozenset(remote), tuple(policies))


def _definition(
    name: str, description: str, properties: dict[str, object], required: list[str]
) -> ToolDefinition:
    return ToolDefinition(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        {"readOnlyHint": True},
    )


def canonical_self_attendance_tool() -> ToolDefinition:
    return _definition(
        SELF_ATTENDANCE_TOOL,
        "List the authenticated requester's attendance for at most 31 inclusive days.",
        {
            "start_date": {"type": "string", "format": "date"},
            "end_date": {"type": "string", "format": "date"},
            "reply_language": {"type": "string", "enum": ["en", "sl"]},
        },
        ["start_date", "end_date", "reply_language"],
    )


def canonical_other_attendance_tool() -> ToolDefinition:
    return _definition(
        "get_other_attendance",
        "For an exact employee ID, username, or email, list attendance for at most 31 inclusive "
        "days. Use exactly one selector.",
        {
            "employee_id": {"type": "string"},
            "username": {"type": "string"},
            "email": {"type": "string"},
            "start_date": {"type": "string", "format": "date"},
            "end_date": {"type": "string", "format": "date"},
            "reply_language": {"type": "string", "enum": ["en", "sl"]},
        },
        ["start_date", "end_date", "reply_language"],
    )


def canonical_current_attendance_tool() -> ToolDefinition:
    return _definition(
        CURRENT_ATTENDANCE_TOOL,
        "List current workforce attendance for one status; server time is authoritative.",
        {
            "status": {"type": "string", "enum": sorted(_STATUS)},
            "reply_language": {"type": "string", "enum": ["en", "sl"]},
        },
        ["status", "reply_language"],
    )


def _valid_tool(tool: DiscoveredMcpTool) -> bool:
    return (
        isinstance(tool.name, str)
        and bool(_TOOL_NAME.fullmatch(tool.name))
        and isinstance(tool.description, str)
        and len(tool.description) <= 4096
        and isinstance(tool.input_schema, Mapping)
        and isinstance(tool.annotations, Mapping)
        and tool.annotations.get("readOnlyHint") is True
    )


def _bounded_dates(values: Mapping[str, str]) -> ListMyAttendanceArguments:
    return ListMyAttendanceArguments(
        start_date=date.fromisoformat(values["start_date"]),
        end_date=date.fromisoformat(values["end_date"]),
    )


def _properties(schema: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if (
        schema is None
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), Mapping)
    ):
        return None
    return schema["properties"]  # type: ignore[return-value]


def _compatible_self(schema: Mapping[str, object] | None) -> bool:
    props = _properties(schema)
    return (
        props is not None
        and all(name in props for name in ("start_date", "end_date", "limit", "offset"))
        and not {"employee_id", "email"}.intersection(props)
    )


def _compatible_resolver(schema: Mapping[str, object] | None) -> bool:
    props = _properties(schema)
    return props is not None and all(name in props for name in ("employee_id", "username", "email"))


def _compatible_other(schema: Mapping[str, object] | None) -> bool:
    props = _properties(schema)
    return props is not None and all(
        name in props for name in ("employee_id", "start_date", "end_date", "limit", "offset")
    )


def _compatible_current(schema: Mapping[str, object] | None) -> bool:
    props = _properties(schema)
    return (
        props is not None
        and all(name in props for name in ("status", "limit", "offset"))
        and "as_of" not in props
    )
