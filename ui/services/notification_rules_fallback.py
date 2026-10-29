"""Fachada de compatibilidad hacia atrás para notification_rules_fallback.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.rules.notification_rules_fallback
"""
import sys
import importlib

_target = importlib.import_module("core.domain.rules.notification_rules_fallback")
sys.modules[__name__] = _target
