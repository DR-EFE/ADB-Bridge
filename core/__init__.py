"""Paquete Núcleo del Sistema Android Tools (Arquitectura por Capas).

Capas:
- core.domain: Modelos, Entidades, Enums y Reglas del Dominio.
- core.infrastructure: Adaptadores para ADB, Base de Datos SQLite, Red, Ollama IA y Sistema.
- core.services: Servicios de Aplicación y Orquestación de Casos de Uso.
"""

from . import domain
from . import infrastructure
from . import services

__all__ = ["domain", "infrastructure", "services"]
