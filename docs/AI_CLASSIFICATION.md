# Clasificación local de notificaciones

La Fase A clasifica cada notificación nueva de forma multietiqueta. No genera
resúmenes, no analiza backups y no crea reglas de automatización.

## Contrato

`NotificationClassification` contiene:

- `labels`: lista de etiquetas `NORMAL`, `AFECTO`, `ROMANTICO`, `COQUETEO`,
  `SEXUAL`, `OFENSIVO`, `URGENTE`, `OTP`, `BANCO`, `SPAM` y `SISTEMA`.
- `primary`: etiqueta principal.
- `confidence`: confianza entre `0` y `1`.
- `reason`: explicación breve.
- `extract`: `otp` e `amount` cuando aparecen literalmente.
- `model`, `package`, `text`, `ts` y `source` (`ollama` o `rules`).

Se eligió una lista multietiqueta porque una notificación puede ser, por ejemplo,
`[BANCO, URGENTE]` o `[ROMANTICO, AFECTO]`.

## Umbrales

- `>= 0.75`: clasificación fuerte.
- `0.50--0.74`: clasificación para revisión.
- `< 0.50`: se conserva la clasificación por reglas para no generar alertas débiles.

Si Ollama no está instalado, no responde o devuelve JSON inválido, el monitor no
se detiene: usa regex y keywords locales. La clasificación se ejecuta en un hilo
separado y se adjunta al registro diario como campo `ai`.

## Configuración

Variables de entorno compatibles con la instalación local:

```text
ANDROID_AI_ENABLED=true
ANDROID_AI_MODEL=qwen2.5-coder:3b
UI_AUTOMATION_OLLAMA_URL=http://127.0.0.1:11434
ANDROID_AI_STRONG_THRESHOLD=0.75
ANDROID_AI_REVIEW_THRESHOLD=0.50
```

`ANDROID_AI_ENABLED=false` desactiva las llamadas a Ollama y mantiene el fallback
por reglas. El monitor no reclasifica dos veces la misma clave durante una sesión.

Ejemplo del campo persistido:

```json
"ai": {
  "labels": ["OTP", "BANCO"],
  "primary": "OTP",
  "confidence": 0.98,
  "reason": "Clasificación determinista por reglas...",
  "extract": {"otp": "123456", "amount": null},
  "source": "rules"
}
```
