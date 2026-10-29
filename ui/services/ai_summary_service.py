"""Fachada de compatibilidad hacia atrás para ai_summary_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.ai_summary_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.ai_summary_service")
sys.modules[__name__] = _target
