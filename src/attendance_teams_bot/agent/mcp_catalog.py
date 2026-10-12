"""Bot-owned admission and execution policy for the authenticated MCP catalog."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from attendance_teams_bot.agent.language_model import ReplyLanguage, ToolDefinition
from attendance_teams_bot.mcp.contracts import (
    CURRENT_WORK_STATUS_TOOL,
    OTHER_ATTENDANCE_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SEARCH_EMPLOYEES_TOOL,
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
        "get_current_work_status",
        "get_employee_attendance_analysis",
        "get_employee_attendance_summary",
        "get_exceptions",
        "get_organization_attendance_analysis",
        "list_employees",
        "get_employee",
        "list_punch_types",
        "list_locations",
        "resolve_employee",
        "search_employees",
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveredMcpTool:
    name: object
    description: object
    input_schema: object
    annotations: object
    meta: object = None


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    definition: ToolDefinition
    kind: Literal["self", "other", "current", "search"]

    def validate_arguments(
        self, arguments: Mapping[str, object]
    ) -> tuple[dict[str, object], ReplyLanguage] | None:
        # Older providers may return this bot-only field; it is never advertised
        # to a provider or forwarded to MCP.
        arguments = {key: value for key, value in arguments.items() if key != "reply_language"}
        if self.kind == "current":
            return self._validate_current_arguments(arguments)
        if not all(
            isinstance(key, str) and isinstance(value, str) and len(value) <= 254
            for key, value in arguments.items()
        ):
            return None
        values = {key: value for key, value in arguments.items() if isinstance(value, str)}
        language: ReplyLanguage = "en"
        try:
            if self.kind == "self":
                if set(arguments) != {"start_date", "end_date"}:
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
                required = {"start_date", "end_date"}
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
            if self.kind == "search":
                if set(arguments) != {"query"} or not values["query"].strip():
                    return None
                return ({"query": values["query"].strip(), "limit": 10}, language)
        except KeyError, ValueError:
            return None

    @staticmethod
    def _validate_current_arguments(
        arguments: Mapping[str, object],
    ) -> tuple[dict[str, object], ReplyLanguage] | None:
        language: ReplyLanguage = "en"
        if not arguments:
            return {}, language
        if set(arguments) != {"statuses"}:
            return None
        statuses = arguments["statuses"]
        if (
            not isinstance(statuses, list | tuple)
            or not statuses
            or len(statuses) > len(_STATUS)
            or not all(isinstance(status, str) and status in _STATUS for status in statuses)
            or len(set(statuses)) != len(statuses)
        ):
            return None
        return {"statuses": tuple(statuses)}, language


@dataclass(frozen=True, slots=True)
class AdmittedMcpCatalog:
    admitted_tool_names: frozenset[str]
    policies: tuple[ToolPolicy, ...]

    @property
    def model_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(policy.definition for policy in self.policies)

    def selected_tool(self, name: str) -> ToolPolicy | None:
        return next((policy for policy in self.policies if policy.definition.name == name), None)


def pre_auth_tool_policies() -> tuple[ToolPolicy, ...]:
    """Pre-auth routing intentionally has no tool definitions."""
    return ()


def admit_mcp_catalog(discovered_tools: Sequence[DiscoveredMcpTool]) -> AdmittedMcpCatalog | None:
    remote: dict[str, DiscoveredMcpTool] = {}
    for tool in discovered_tools:
        if not isinstance(tool.name, str) or tool.name not in _ALLOWED_READ_ONLY_TOOL_NAMES:
            continue
        if tool.name in remote or not _valid_tool(tool):
            return None
        remote[tool.name] = tool
    if not _compatible_self(_schema(remote.get(SELF_ATTENDANCE_TOOL))):
        return None
    policies = [ToolPolicy(_remote_definition(remote[SELF_ATTENDANCE_TOOL]), "self")]
    if _compatible_resolver(_schema(remote.get(RESOLVE_EMPLOYEE_TOOL))) and _compatible_other(
        _schema(remote.get(OTHER_ATTENDANCE_TOOL))
    ):
        policies.append(ToolPolicy(_other_definition(remote), "other"))
    if _compatible_current(_schema(remote.get(CURRENT_WORK_STATUS_TOOL))):
        policies.append(ToolPolicy(_remote_definition(remote[CURRENT_WORK_STATUS_TOOL]), "current"))
    if _compatible_search(_schema(remote.get(SEARCH_EMPLOYEES_TOOL))):
        policies.append(ToolPolicy(_remote_definition(remote[SEARCH_EMPLOYEES_TOOL]), "search"))
    return AdmittedMcpCatalog(frozenset(remote), tuple(policies))


def _schema(tool: DiscoveredMcpTool | None) -> Mapping[str, object] | None:
    if tool is None or not isinstance(tool.input_schema, Mapping):
        return None
    return tool.input_schema


def _remote_definition(tool: DiscoveredMcpTool) -> ToolDefinition:
    """Pass admitted server metadata through untouched to the provider boundary."""
    assert isinstance(tool.name, str)
    assert isinstance(tool.description, str)
    assert isinstance(tool.input_schema, Mapping)
    assert isinstance(tool.annotations, Mapping)
    meta = tool.meta if isinstance(tool.meta, Mapping) else {}
    return ToolDefinition(tool.name, tool.description, tool.input_schema, tool.annotations, meta)


def _other_definition(remote: Mapping[str, DiscoveredMcpTool]) -> ToolDefinition:
    """Expose the bot-owned composite only when both authoritative source tools exist."""
    resolver, attendance = remote[RESOLVE_EMPLOYEE_TOOL], remote[OTHER_ATTENDANCE_TOOL]
    assert isinstance(resolver.description, str) and isinstance(attendance.description, str)
    return ToolDefinition(
        "get_other_attendance",
        f"{resolver.description}\n{attendance.description}",
        {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "username": {"type": "string"},
                "email": {"type": "string"},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
            },
            "required": ["start_date", "end_date"],
            "additionalProperties": False,
        },
        {"readOnlyHint": True},
        {"source_tools": (resolver.meta, attendance.meta)},
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
        and (tool.meta is None or isinstance(tool.meta, Mapping))
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
    return props is not None and all(name in props for name in ("statuses", "limit", "offset"))


def _compatible_search(schema: Mapping[str, object] | None) -> bool:
    props = _properties(schema)
    return props is not None and all(name in props for name in ("query", "limit"))
