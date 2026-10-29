"""Fachada de compatibilidad hacia atrás para ai_report_models.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.models.ai
"""
import sys
import importlib

_target = importlib.import_module("core.domain.models.ai")
sys.modules[__name__] = _target
