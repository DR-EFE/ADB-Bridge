# Automatizaciones por Reglas (Fase 4B)

El motor de automatización (`AutomationService`) permite configurar y disparar flujos automáticos (respaldos, sincronización inteligente, registro de notificaciones, métricas y diagnósticos ligeros) ante eventos del ciclo de vida del dispositivo y la aplicación.

---

## 1. Arquitectura y Seguridad

```text
                  ┌────────────────────────┐
                  │    Eventos de Entrada  │
                  │ - on_device_connected  │
                  │ - on_notification      │
                  │ - on_storage_low       │
                  └───────────┬────────────┘
                              │
                              ▼
                   ┌──────────────────────┐
                   │  AutomationService   │
                   └──────────┬───────────┘
                              │ Evalúa reglas (automation_rules)
                              ▼
           ┌──────────────────────────────────────┐
           │          Acciones Seguras            │
           ├──────────────────┬───────────────────┤
           │ RUN_SYNC_PROFILE │ SyncService       │
           │ RUN_BACKUP_PROF. │ BackupService     │
           │ SAVE_NOTIFICAT.  │ SQLite (notif.)   │
           │ LOG_SUMMARY      │ SQLite / Logger   │
           │ SHELL_SAFE_CMD   │ AdbService        │
           └──────────────────┴───────────────────┘
                              │
                              ▼
                     JobQueueService (Async)
```

### Seguridad y Restricciones
- **No ejecución arbitraria**: La acción `SHELL_SAFE_COMMAND` únicamente acepta comandos pre-aprobados en `SAFE_SHELL_ALLOWLIST` (`uptime`, `date`, `df -h /sdcard`, `getprop ro.build.version.release`, `dumpsys battery`). Cualquier comando fuera de la lista es bloqueado inmediatamente.
- **No bloqueo**: Los eventos se evalúan de forma ligera y despachan los trabajos pesados a `JobQueueService`.
- **Aislamiento**: Tablas SQLite con prefijo exclusivo `automation_*` (`automation_rules`, `automation_runs`).

---

## 2. Componentes de una Regla

### Disparadores (`TriggerType`)
- `device_connected`: Al establecer conexión y estar en estado `device`.
- `notification_received`: Al recibir una notificación en el monitor.
- `storage_low`: Cuando el espacio libre en el teléfono cae bajo el umbral configurado.
- `schedule`: Programación periódica.

### Condiciones (`AutomationCondition`)
- `serial`: Filtrar por serial del dispositivo.
- `package_name`: Filtrar por paquete de la app de origen (ej. `com.whatsapp`, `com.banco`).
- `text_contains`: Subcadena obligatoria en el contenido de la notificación o evento.
- `profile_id`: Perfil objetivo.
- `storage_threshold_percent`: Umbral de espacio disponible (ej. `< 15%`).

### Acciones (`ActionType`)
1. `run_backup_profile`: Invoca `backup_service.enqueue_profile` o encola en `JobQueueService`.
2. `run_sync_profile`: Invoca `sync_service.enqueue_sync_job`.
3. `save_notification`: Registra el evento en la tabla `notifications`.
4. `log_summary`: Emite un mensaje de auditoría y métrica.
5. `shell_safe_command`: Ejecuta una consulta ligera de sólo lectura autorizada.

---

## 3. Ejemplo Práctico: Al conectar → Sync Fotos + Backup Documentos

### Definición y Registro de la Regla

```python
from ui.services.automation_rules import (
    AutomationRule, TriggerType, AutomationCondition,
    AutomationAction, ActionType
)
from ui.services.automation_service import AutomationService

automation = AutomationService(
    job_queue=job_queue,
    sync_service=sync_service,
    backup_service=backup_service,
)

rule = AutomationRule(
    id="rule_connect_suite",
    name="Al conectar -> Sync fotos nuevas + Backup documentos",
    trigger=TriggerType.DEVICE_CONNECTED,
    conditions=AutomationCondition(
        serial="192.168.1.50:5555"  # o None para cualquier dispositivo
    ),
    actions=[
        AutomationAction(
            action_type=ActionType.RUN_SYNC_PROFILE,
            params={"profile_id": "new_photos"}
        ),
        AutomationAction(
            action_type=ActionType.RUN_BACKUP_PROFILE,
            params={"profile_id": "docs_backup"}
        ),
        AutomationAction(
            action_type=ActionType.LOG_SUMMARY,
            params={"message": "Sincronización y respaldo disparados al conectar el celular."}
        )
    ]
)

# Guardar en SQLite (tabla automation_rules)
automation.save_rule(rule)
```

### Inyección del Evento

```python
# Llamado desde el monitor de conexión al detectar estado 'device'
runs = automation.on_device_connected(serial="192.168.1.50:5555")

# Generar resumen JSON
summary = automation.generate_summary(runs)
print(summary)
```

---

## 4. Estructura de Salida (JSON de Auditoría)

```json
{
  "total_runs": 1,
  "success": 1,
  "failed": 0,
  "partial": 0,
  "timestamp_utc": "2026-10-21T03:00:00Z",
  "runs": [
    {
      "run_id": "run_98fbc1230e54",
      "rule_id": "rule_connect_suite",
      "trigger_type": "device_connected",
      "status": "success",
      "summary_message": "Regla 'Al conectar -> Sync fotos nuevas + Backup documentos' ejecutada: 3 acciones evaluadas (success).",
      "actions_executed": [
        {
          "action_type": "run_sync_profile",
          "status": "success",
          "job_id": "job_01a2b3c4d5e6",
          "output": "Sync perfil 'new_photos' encolado con éxito (Job: job_01a2b3c4d5e6)"
        },
        {
          "action_type": "run_backup_profile",
          "status": "success",
          "job_id": "job_99f8e7d6c5b4",
          "output": "Backup profile 'docs_backup' encolado (Job: job_99f8e7d6c5b4)"
        },
        {
          "action_type": "log_summary",
          "status": "success",
          "output": "Sincronización y respaldo disparados al conectar el celular."
        }
      ]
    }
  ]
}
```
