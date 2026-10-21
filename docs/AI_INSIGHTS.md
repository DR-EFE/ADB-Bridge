# Módulo de Inteligencia Artificial Local (Fase B — Insights, Resumen y Sugerencias)

Este módulo provee capacidades de análisis inteligente, resumen de actividad y sugerencias proactivas de automatización para la administración de dispositivos Android locales, ejecutándose de forma 100% local a través de **Ollama** (ej. modelo `qwen2.5-coder:7b`) con **resiliencia total determinista** (fallback automático sin fallar cuando Ollama está apagado o no responde).

---

## 1. Principios de Diseño y Frontera de Seguridad

- **100% Local y Privado**: No se envía telemetría ni datos a servicios externos en la nube. Todas las inferencias se ejecutan en Ollama local (`http://127.0.0.1:11434`).
- **No Invasivo / Sin Ejecución Arbitraria**: El módulo de IA **nunca** ejecuta comandos shell arbitrarios ni modifica perfiles existentes de respaldo o sincronización.
- **Aprobación Humana Obligatoria (`confirm_required=True`)**: Las sugerencias de automatización generadas por IA son **únicamente propuestas**. Jamás se aplican automáticamente ni mutan el estado del sistema hasta que el usuario invoque explícitamente `accept_suggestion()`.
- **Degradación Determinista (Graceful Fallback)**: Si Ollama no está disponible o falla por timeout/conexión, todos los servicios continúan funcionando normalmente entregando resúmenes estructurados y recomendaciones basadas en plantillas heurísticas.

---

## 2. Modelos de Datos (`ui/services/ai_report_models.py`)

### `DaySummary`
Resumen consolidado de notificaciones para un día específico:
- `date`: Fecha analizada (`YYYY-MM-DD`).
- `total_notifications`: Conteo total de eventos procesados.
- `app_counts`: Diccionario con la distribución de eventos por paquete o aplicación.
- `urgent_count`: Conteo de alertas urgentes o bancarias identificadas.
- `otp_count`: Conteo de códigos de verificación temporal (OTP) detectados.
- `urgent_and_otp_items`: Lista estructurada de eventos críticos para auditoría rápida.
- `highlights`: Viñetas explicativas en español generadas por Ollama o por el fallback determinista.
- `generated_by_llm`: Booleano que indica si las viñetas fueron redactadas por el LLM o por extracción determinista.
- `model_used`: Nombre del modelo Ollama empleado si aplica.

### `BackupAnalysisReport`
Diagnóstico forense y consolidado sobre uno o más respaldos locales:
- `analysis_id`: Identificador único del reporte.
- `backup_directories`: Directorios analizados.
- `total_backups_analyzed`: Cantidad de respaldos inspeccionados.
- `total_errors`: Número total de fallos encontrados (`errors.json` y categorías `DENIED`/`PARTIAL`).
- `errors_by_category`: Agrupación de errores por categoría.
- `repeated_denied_paths`: Rutas o recursos que fallaron recurrentemente ($\ge$ umbral).
- `findings`: Lista de hallazgos tipados (`BackupFinding`) con severidad (`INFO`, `WARNING`, `ERROR`), conteo de fallos y descripción técnica.
- `recommendations`: Sugerencias accionables (`BackupRecommendation`) basadas en reglas y pulidas por LLM.
- `summary_text`: Resumen ejecutivo en español.

### `AutomationSuggestion`
Propuesta controlada de regla para el motor de automatizaciones:
- `id`: Identificador de la sugerencia (ej. `sug_a1b2c3d4`).
- `title`: Título legible de la propuesta.
- `rationale`: Justificación técnica en español explicando por qué la regla le conviene al usuario.
- `trigger_type`: Tipo de disparador propuesto (`DEVICE_CONNECTED`, `NOTIFICATION_RECEIVED`, etc.).
- `proposed_rule`: Diccionario serializable con la estructura exacta requerida por `AutomationRule`.
- `confirm_required`: Siempre `True`.
- `status`: Estado actual del flujo (`pending`, `accepted`, `dismissed`).
- `signature`: Firma heurística para evitar propuestas duplicadas o spam.
- `dismissed_until_utc`: Marca de tiempo hasta la cual la sugerencia permanecerá en enfriamiento si fue descartada.

---

## 3. Servicios y Guía de Uso

### 3.1 Resumen del Día de Notificaciones (`AiSummaryService`)

Lee los archivos diarios `notifications/notifications_YYYY-MM-DD.json` y `notifications/notificaciones_importantes.json`. Agrega los conteos, detecta códigos de verificación y genera viñetas ejecutivas en español. Guarda el resultado en `notifications/summary_YYYY-MM-DD.json`.

```python
from ui.services.ai_summary_service import AiSummaryService
from ui.services.ollama_client import OllamaClient

# Cliente Ollama inyectable (o None para usar cliente predeterminado)
ollama = OllamaClient(base_url="http://127.0.0.1:11434", default_model="qwen2.5-coder:7b")

service = AiSummaryService(ollama_client=ollama)

# Generar resumen bajo demanda para una fecha
summary = service.generate_day_summary("2026-10-21")

print(f"Total notificaciones: {summary.total_notifications}")
print(f"Alertas urgentes: {summary.urgent_count} | OTPs detectados: {summary.otp_count}")
print("Viñetas resumen:")
for h in summary.highlights:
    print(f"  {h}")
```

---

### 3.2 Analizador de Reportes de Respaldo (`BackupAnalyzerService`)

Inspecciona los artefactos `MANIFEST.json` y `errors.json` generados durante los respaldos. Detecta bloqueos por permisos (`DENIED`), respaldos parciales (`PARTIAL`), y correlaciona rutas que fallan múltiples veces consecutivas (ej. restricciones de Scoped Storage en Android 11+ o bases de datos de proveedores protegidas).

```python
from pathlib import Path
from ui.services.backup_analyzer_service import BackupAnalyzerService

service = BackupAnalyzerService(threshold_repeated=2)

backup_dirs = [
    Path("backups/backup_20261020_120000"),
    Path("backups/backup_20261021_120000"),
]

report = service.analyze_backups(backup_dirs)

print(f"Total respaldos analizados: {report.total_backups_analyzed}")
print(f"Fallos registrados: {report.total_errors}")
print(f"Rutas críticas repetidas: {report.repeated_denied_paths}")

for rec in report.recommendations:
    print(f"[{rec.category}] Sugerencia: {rec.recommendation}")

print(f"\nResumen ejecutivo: {report.summary_text}")
```

---

### 3.3 Sugerencias de Automatización (`AutomationSuggestionService`)

Evalúa heurísticas seguras sobre eventos reales:
1. **Transacciones bancarias / compras**: Sugiere registrar una auditoría local de la alerta.
2. **Conexiones frecuentes de un dispositivo**: Sugiere programar la sincronización incremental de fotos al conectarlo.
3. **Fallos persistentes en respaldos**: Sugiere una verificación ligera de almacenamiento al conectar el terminal.

#### Generar y listar sugerencias pendientes:
```python
from ui.services.automation_suggestion_service import AutomationSuggestionService
from ui.services.automation_service import AutomationService

automation_service = AutomationService(...)  # Servicio de reglas existente
suggestion_service = AutomationSuggestionService(automation_service=automation_service)

# Genera sugerencias basadas en eventos recientes
nuevas = suggestion_service.generate_suggestions()

# Listar sugerencias que requieren confirmación
pendientes = suggestion_service.list_suggestions(status_filter=AutomationSuggestionStatus.PENDING)
for s in pendientes:
    print(f"ID: {s.id} | Título: {s.title}")
    print(f"Justificación: {s.rationale}\n")
```

#### Aceptar una sugerencia (Conversión segura a regla):
```python
# ÚNICAMENTE cuando el usuario hace clic o confirma explícitamente:
sugerencia_aceptada = suggestion_service.accept_suggestion(suggestion_id="sug_3f8a1290")
# La regla se valida contra AutomationRule y se registra en AutomationService.
```

#### Descartar una sugerencia con período de enfriamiento (Cooldown):
```python
# El usuario descarta la sugerencia; no se volverá a proponer la misma firma en 7 días
suggestion_service.dismiss_suggestion(suggestion_id="sug_3f8a1290", cooldown_days=7)
```

---

## 4. Pruebas Unitarias y Validación

Los tests se encuentran en `tests/unit/`:
- `test_ai_summary_service.py`: Prueba conteos, detección de OTPs, alertas, fallbacks y persistencia de `summary_YYYY-MM-DD.json`.
- `test_backup_analyzer_service.py`: Prueba correlación de fallos en múltiples backups, detección de rutas repetidas, generación de hallazgos y plantillas.
- `test_automation_suggestion_service.py`: Prueba generación heurística de propuestas, obligatoriedad de confirmación humana, guardado seguro de reglas al aceptar y cooldown tras descarte.

Para ejecutar toda la suite de pruebas:
```powershell
pytest tests/unit/test_ai_summary_service.py tests/unit/test_backup_analyzer_service.py tests/unit/test_automation_suggestion_service.py -v
```
