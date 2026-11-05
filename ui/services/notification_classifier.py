"""Fachada de compatibilidad hacia atrás para notification_classifier.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.notification_classifier
"""
import sys
import importlib

_target = importlib.import_module("core.services.notification_classifier")
sys.modules[__name__] = _target
