"""Importing this package registers every built-in plugin.

To add a plugin: create a module here with a ``@register``-decorated ``Plugin``
subclass and add it to the import list below.
"""
from . import ai, alerts, mailer, performance, report, ticketing  # noqa: F401
from .base import PluginContext, all_plugins, get_plugin  # noqa: F401
