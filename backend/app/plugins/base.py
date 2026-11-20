"""Plugin architecture.

A plugin is an integration (data source, ticketing system, mailer, LLM ...) that
exposes one or more *actions*. Workflow steps reference ``plugin`` + ``action``;
the execution engine looks them up in the registry at runtime, so new
integrations are added by dropping a module into ``app/plugins`` and decorating
the class with ``@register`` - no engine changes required.
"""
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session


@dataclass
class PluginContext:
    db: Session
    run_id: int | None = None
    config: dict = field(default_factory=dict)  # per-plugin config from the plugins table


@dataclass
class ActionSpec:
    name: str
    description: str
    inputs: dict[str, str]   # input name -> human description (type hints for LLM / UI)
    outputs: dict[str, str]
    fn: Callable[..., dict]
    required: tuple[str, ...] = ()   # inputs that must be provided by the step
    side_effect: bool = False        # sends/creates something outside the platform


class Plugin:
    name: str = ""
    description: str = ""
    category: str = "general"

    def __init__(self) -> None:
        self.actions: dict[str, ActionSpec] = {}
        for attr in dir(self):
            meth = getattr(self, attr)
            spec = getattr(meth, "_action_spec", None)
            if spec:
                self.actions[spec["name"]] = ActionSpec(fn=meth, **spec)

    def mode(self) -> str:
        """'live' when talking to a real external system, 'simulated' otherwise."""
        return "live"

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "mode": self.mode(),
            "actions": [
                {"name": a.name, "description": a.description, "inputs": a.inputs, "outputs": a.outputs,
                 "required_inputs": list(a.required), "side_effect": a.side_effect}
                for a in self.actions.values()
            ],
        }

    def run(self, action: str, ctx: PluginContext, **inputs: Any) -> dict:
        if action not in self.actions:
            raise KeyError(f"plugin '{self.name}' has no action '{action}'")
        return self.actions[action].fn(ctx, **inputs)


def action(name: str, description: str, inputs: dict[str, str] | None = None,
           outputs: dict[str, str] | None = None, required: tuple[str, ...] = (), side_effect: bool = False):
    def deco(fn):
        fn._action_spec = {"name": name, "description": description, "inputs": inputs or {},
                           "outputs": outputs or {}, "required": tuple(required), "side_effect": side_effect}
        return fn
    return deco


_REGISTRY: dict[str, Plugin] = {}


def register(cls: type[Plugin]) -> type[Plugin]:
    inst = cls()
    _REGISTRY[inst.name] = inst
    return cls


def get_plugin(name: str) -> Plugin:
    if name not in _REGISTRY:
        raise KeyError(f"unknown plugin '{name}'")
    return _REGISTRY[name]


def all_plugins() -> list[Plugin]:
    return list(_REGISTRY.values())
