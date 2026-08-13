"""Everyday utilities — REQ-18.

These run locally and deterministically. Arithmetic and unit conversion are
exactly the things a language model is worst at and a library is perfect at, so
the router is told to reach for these rather than answering from the model.
"""

from __future__ import annotations

import ast
import operator
from datetime import datetime, timedelta, timezone
from typing import Any

from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

MAX_EXPONENT = 1_000_000


class CalculateSkill(Skill):
    name = "utils.calculate"
    description = (
        "Evaluate an arithmetic expression exactly. Use this for any sum, percentage, "
        "or numeric comparison instead of working it out yourself."
    )
    parameters = (
        SkillParam("expression", "string", "An arithmetic expression, e.g. '(1200 * 0.21) + 45'."),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        expression = str(args["expression"]).strip().replace("×", "*").replace("÷", "/").replace("^", "**")
        if not expression:
            raise SkillError("There was no expression to calculate.")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise SkillError(f"'{expression}' isn't a valid expression.") from exc

        value = _eval_node(tree.body)
        rendered = f"{value:,.10g}" if isinstance(value, float) else f"{value:,}"
        return SkillResult(
            ok=True,
            message=f"{expression} = {rendered}",
            data={"expression": expression, "value": value},
        )


def _eval_node(node: ast.AST) -> Any:
    """A closed arithmetic evaluator — no names, calls, attributes or subscripts.

    Written as an allowlist so that anything not explicitly permitted is refused;
    the expression string originates from a language model.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise SkillError("Only numbers are allowed in expressions.")

    if isinstance(node, ast.BinOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise SkillError("That operator isn't supported.")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 64 or abs(left) ** abs(right) > MAX_EXPONENT ** 4):
            raise SkillError("That power is too large to compute.")
        try:
            return op(left, right)
        except ZeroDivisionError as exc:
            raise SkillError("That divides by zero.") from exc

    if isinstance(node, ast.UnaryOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise SkillError("That operator isn't supported.")
        return op(_eval_node(node.operand))

    raise SkillError("Only plain arithmetic is allowed here.")


class ConvertUnitsSkill(Skill):
    name = "utils.convert"
    description = (
        "Convert between units — length, mass, volume, temperature, time, data size. "
        "Example: 180 lb to kg, 22 C to F, 1.5 GB to MB."
    )
    parameters = (
        SkillParam("value", "number", "The quantity to convert."),
        SkillParam("from_unit", "string", "The unit it is in now, e.g. 'lb'."),
        SkillParam("to_unit", "string", "The unit to convert to, e.g. 'kg'."),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        try:
            import pint
        except ImportError as exc:  # pragma: no cover
            raise SkillError("Unit conversion isn't available (pint not installed).") from exc

        registry = _unit_registry(pint)
        value = float(args["value"])
        from_unit = str(args["from_unit"]).strip()
        to_unit = str(args["to_unit"]).strip()

        try:
            quantity = registry.Quantity(value, from_unit)
            converted = quantity.to(to_unit)
        except Exception as exc:  # noqa: BLE001 — pint raises many types
            raise SkillError(f"I can't convert {from_unit} to {to_unit}.") from exc

        magnitude = converted.magnitude
        rendered = f"{magnitude:,.6g}"
        return SkillResult(
            ok=True,
            message=f"{value:g} {from_unit} = {rendered} {to_unit}",
            data={"value": magnitude, "unit": to_unit},
        )


_registry_cache: Any = None


def _unit_registry(pint_module: Any) -> Any:
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = pint_module.UnitRegistry()
    return _registry_cache


class TimeSkill(Skill):
    name = "utils.time"
    description = (
        "Current date and time, time in another zone, or date arithmetic "
        "('how many days until 2026-12-25')."
    )
    parameters = (
        SkillParam("timezone_name", "string", "IANA zone, e.g. 'Europe/Madrid'.", required=False),
        SkillParam("until_date", "string", "An ISO date to count towards, e.g. '2026-12-25'.",
                   required=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        now_local = datetime.now().astimezone()
        parts = [f"Local time: {now_local.strftime('%Y-%m-%d %H:%M %Z')}"]
        data: dict[str, Any] = {"local": now_local.isoformat()}

        zone_name = str(args.get("timezone_name", "") or "").strip()
        if zone_name:
            try:
                from zoneinfo import ZoneInfo

                zoned = datetime.now(ZoneInfo(zone_name))
            except Exception:  # noqa: BLE001
                # Not an error. The router fills this in unasked -- "what time is
                # it?" arrives carrying the machine's Windows zone name, which is
                # not IANA and never resolves -- so raising here would fail the
                # simplest question the assistant can be asked, over an argument
                # the user never supplied. Local time is what was wanted; say the
                # zone was not recognised and answer anyway.
                parts.append(f"(I don't recognise the time zone '{zone_name}'.)")
                data["unknown_zone"] = zone_name
            else:
                parts.append(f"{zone_name}: {zoned.strftime('%Y-%m-%d %H:%M %Z')}")
                data["zoned"] = zoned.isoformat()

        target_raw = str(args.get("until_date", "") or "").strip()
        if target_raw:
            try:
                target = datetime.fromisoformat(target_raw)
            except ValueError as exc:
                raise SkillError(f"'{target_raw}' isn't a date I can read (use YYYY-MM-DD).") from exc
            if target.tzinfo is None:
                target = target.replace(tzinfo=now_local.tzinfo)
            delta: timedelta = target - now_local
            days = delta.days
            direction = "from now" if days >= 0 else "ago"
            parts.append(f"{target_raw} is {abs(days)} days {direction}.")
            data["days_until"] = days

        return SkillResult(ok=True, message="\n".join(parts), data=data)


class CurrencySkill(Skill):
    name = "utils.currency"
    description = "Convert between currencies at today's rate, e.g. 250 EUR to USD."
    parameters = (
        SkillParam("amount", "number", "How much."),
        SkillParam("from_currency", "string", "Three-letter code, e.g. 'EUR'."),
        SkillParam("to_currency", "string", "Three-letter code, e.g. 'USD'."),
    )
    requires = ("live_data",)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        import httpx

        amount = float(args["amount"])
        source = str(args["from_currency"]).strip().upper()
        target = str(args["to_currency"]).strip().upper()

        try:
            response = httpx.get(f"https://open.er-api.com/v6/latest/{source}", timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SkillError("I couldn't reach the exchange rate service just now.") from exc

        if payload.get("result") != "success":
            raise SkillError(f"'{source}' doesn't look like a currency I can price.")

        rates = payload.get("rates") or {}
        rate = rates.get(target)
        if rate is None:
            raise SkillError(f"I don't have a rate for {source} to {target}.")

        converted = amount * float(rate)
        as_of = payload.get("time_last_update_utc", "unknown time")
        return SkillResult(
            ok=True,
            # REQ-18: live values always carry their as-of time.
            message=f"{amount:,.2f} {source} = {converted:,.2f} {target} (rate {rate:g}, as of {as_of})",
            data={"amount": converted, "rate": rate, "as_of": as_of},
        )
