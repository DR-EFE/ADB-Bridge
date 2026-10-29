"""Fachada de compatibilidad hacia atrás para notification_history.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.notification_history
"""
import sys
import importlib

_target = importlib.import_module("core.services.notification_history")
sys.modules[__name__] = _target
