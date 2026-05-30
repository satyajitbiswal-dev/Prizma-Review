from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'webhooks'

    def ready(self):
        # Bind runserver to the same Celery app instance as `celery -A config worker`.
        import config  # noqa: F401
