"""Config-driven Prompt-Templates für verschiedene Projekt-Typen."""
from patch_notes.templates.base import BaseTemplate
from patch_notes.templates.gaming import GamingTemplate
from patch_notes.templates.saas import SaaSTemplate
from patch_notes.templates.devops import DevOpsTemplate

_REGISTRY = {
    'gaming': GamingTemplate,
    'saas': SaaSTemplate,
    'devops': DevOpsTemplate,
}

def get_template(template_type: str) -> BaseTemplate:
    """Factory: Template-Klasse nach Typ-String."""
    cls = _REGISTRY.get(template_type, BaseTemplate)
    return cls()
