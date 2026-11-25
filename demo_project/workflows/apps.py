from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    name = "demo_project.workflows"
    label = "workflows"

    def ready(self) -> None:
        from . import signals  # noqa: F401
