"""A Libra plugin template. Copy it, rename the skills, and load it with:

    libra chat --plugins path/to/folder        (or put the file in ~/.libra/plugins/)

Each @skill function becomes a tool: /name in chat, `libra skill run name …`,
POST /v1/skills/name, and a step the goal agent can plan.

risk:      "read"   only looks things up (runs without asking)
           "write"  changes something (you're asked first; this is the default)
           "danger" hard to undo (you're asked first)
external:  True if the skill returns text from outside (a website, another service).
           Libra then treats its output as data: it can't trigger a change without you.
"""
import re

from libra.skills import skill


@skill("shout", "Turn text into capital letters.", {"text": "the text to change"}, risk="read")
def shout(text: str) -> str:
    return text.upper()


def _route_status(message: str):
    """Optional: recognise a chat message or goal step. Return the arguments, or None."""
    m = re.search(r"\bstatus of (\w[\w-]*)", message, re.I)
    return {"service": m.group(1)} if m else None


@skill("service_status", "Look up the status of one of my services.", {"service": "its name"},
       router=_route_status, risk="read", external=True)
def service_status(service: str) -> dict:
    # Replace with a real lookup. Returning a dict or list gives later steps structured data.
    return {"service": service, "status": "unknown (template)"}


@skill("restart_service", "Restart one of my services.", {"service": "its name"}, risk="danger")
def restart_service(service: str) -> str:
    # A "danger" skill only runs after you approve it (or after: libra trust allow restart_service).
    raise NotImplementedError("fill this in")
