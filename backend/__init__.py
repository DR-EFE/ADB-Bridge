"""Backend local FastAPI para el sistema de administración Android.

Expone endpoints REST y WebSockets de progreso de trabajos para control local
sin depender de la interfaz gráfica ni reescribir los servicios de dominio.
"""

from .main import app, create_app

__all__ = ["app", "create_app"]
