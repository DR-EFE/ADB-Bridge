"""Fachada de compatibilidad hacia atrás para automation_rules.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.rules.automation_rules
"""
import sys
import importlib

_target = importlib.import_module("core.domain.rules.automation_rules")
sys.modules[__name__] = _target
