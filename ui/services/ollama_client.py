"""Fachada de compatibilidad hacia atrás para ollama_client.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.ai.ollama_client
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.ai.ollama_client")
sys.modules[__name__] = _target
