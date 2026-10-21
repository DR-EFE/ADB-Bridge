"""Servicio de análisis y diagnóstico de reportes de respaldo (Fase B - IA).

Analiza artefactos generados por el Centro de Respaldos (MANIFEST.json y errors.json)
a través de una o múltiples sesiones de respaldo para:
1. Agregar y cuantificar fallos por categoría, ruta y código de error.
2. Identificar rutas que fallan repetidamente (>= umbral) y denegaciones de permisos (DENIED).
3. Generar hallazgos (findings) y recomendaciones accionables basadas en plantillas deterministas.
4. Generar opcionalmente un resumen narrativo de diagnóstico mediante un modelo LLM local en Ollama.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .ai_report_models import (
    BackupAnalysisReport,
    BackupFinding,
    BackupRecommendation,
)
from .ollama_client import OllamaClient, OllamaError

logger = logging.getLogger("ui.services.backup_analyzer_service")


class BackupAnalyzerService:
    """Analizador determinista con enriquecimiento opcional por LLM para reportes de backup."""

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        model: str = "qwen2.5-coder:7b",
        threshold_repeated: int = 2,
    ) -> None:
        self.ollama = ollama_client or OllamaClient()
        self.model = model
        self.threshold_repeated = threshold_repeated

    def _read_backup_artifacts(self, backup_dir: Path) -> Tuple[Optional[Dict[str, Any]], List[Any]]:
        """Lee MANIFEST.json y errors.json del directorio de respaldo."""
        manifest: Optional[Dict[str, Any]] = None
        errors: List[Any] = []

        manifest_file = backup_dir / "MANIFEST.json"
        if manifest_file.is_file():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Fallo al leer %s: %s", manifest_file, exc)

        errors_file = backup_dir / "errors.json"
        if errors_file.is_file():
            try:
                raw_errors = json.loads(errors_file.read_text(encoding="utf-8"))
                if isinstance(raw_errors, list):
                    errors = raw_errors
            except Exception as exc:
                logger.warning("Fallo al leer %s: %s", errors_file, exc)

        return manifest, errors

    def _generate_recommendations(
        self,
        repeated_denied: List[str],
        category_errors: Dict[str, int],
        path_failure_map: Dict[str, List[Dict[str, Any]]],
    ) -> List[BackupRecommendation]:
        """Genera recomendaciones deterministas estructuradas a partir de los patrones de error."""
        recommendations: List[BackupRecommendation] = []
        rec_id = 1

        # 1. Fallos en SMS, Llamadas o Contactos por restricción del sistema OEM
        restricted_categories = [c for c in ("sms", "calls", "contacts_sms_calls") if category_errors.get(c, 0) > 0]
        if restricted_categories:
            recommendations.append(
                BackupRecommendation(
                    id=f"rec_{rec_id}",
                    category=", ".join(restricted_categories),
                    action_type="check_system_permissions",
                    recommendation=(
                        "Android denegó el acceso a bases de datos de mensajes o llamadas (SecurityException). "
                        "En dispositivos modernos sin root, estas tablas están protegidas por el sistema; "
                        "se recomienda verificar los permisos de la app receptora o realizar exportación manual."
                    ),
                    suggested_action={"check_permissions": True, "categories": restricted_categories},
                )
            )
            rec_id += 1

        # 2. Rutas con fallos reiterados (rutas inexistentes o Scoped Storage)
        for path in repeated_denied:
            sample_entries = path_failure_map.get(path, [])
            cat = sample_entries[0].get("category", "general") if sample_entries else "general"

            if "whatsapp" in path.lower():
                recommendations.append(
                    BackupRecommendation(
                        id=f"rec_{rec_id}",
                        category=cat,
                        action_type="update_whatsapp_path",
                        recommendation=(
                            f"La ruta '{path}' falló reiteradamente. En Android 11+ con Scoped Storage, "
                            "WhatsApp almacena archivos en 'Android/media/com.whatsapp/WhatsApp/Media'. "
                            "Actualice la ruta o excluya la ruta obsoleta si la app ya no la utiliza."
                        ),
                        suggested_action={"path": path, "target": "Android/media/com.whatsapp/WhatsApp/Media"},
                    )
                )
                rec_id += 1
            else:
                recommendations.append(
                    BackupRecommendation(
                        id=f"rec_{rec_id}",
                        category=cat,
                        action_type="exclude_or_verify_path",
                        recommendation=(
                            f"La ruta '{path}' falló reiteradamente ({len(sample_entries)} veces). "
                            "Verifique si la carpeta existe en el almacenamiento o exclúyala para evitar demoras y advertencias."
                        ),
                        suggested_action={"exclude_path": path},
                    )
                )
                rec_id += 1

        return recommendations

    def _generate_llm_summary(
        self,
        total_backups: int,
        total_errors: int,
        category_errors: Dict[str, int],
        repeated_denied: List[str],
        findings_count: int,
        recommendations: List[BackupRecommendation],
    ) -> Optional[str]:
        """Usa Ollama para sintetizar un resumen ejecutivo del diagnóstico."""
        if not self.ollama.is_available():
            return None

        prompt = (
            f"Analiza los siguientes resultados técnicos de respaldo Android:\n"
            f"- Respaldos evaluados: {total_backups}\n"
            f"- Errores totales detectados: {total_errors}\n"
            f"- Errores por categoría: {json.dumps(category_errors, ensure_ascii=False)}\n"
            f"- Rutas denegadas o fallidas repetidamente: {json.dumps(repeated_denied, ensure_ascii=False)}\n"
            f"- Recomendaciones identificadas: {[r.recommendation for r in recommendations]}\n\n"
            "Redacta un resumen de diagnóstico profesional en español de 1 a 2 párrafos concisos. "
            "Explica de forma clara el estado general de los respaldos, la causa raíz de los fallos "
            "(como permisos de Android o rutas modificadas por Scoped Storage) y los pasos principales "
            "para garantizar que los próximos respaldos se completen al 100%."
        )

        try:
            summary = self.ollama.generate(
                prompt=prompt,
                model=self.model,
                temperature=0.2,
                timeout=25.0,
            )
            if summary and len(summary.strip()) > 30:
                return summary.strip()
        except OllamaError as exc:
            logger.info("Ollama no completó la redacción del resumen de backup: %s", exc)
        except Exception as exc:
            logger.warning("Fallo al consultar Ollama para análisis de backup: %s", exc)

        return None

    def analyze_backups(
        self,
        backup_dirs: List[Path | str],
        threshold_repeated: Optional[int] = None,
    ) -> BackupAnalysisReport:
        """
        Analiza uno o más directorios de respaldo leyendo sus artefactos MANIFEST.json y errors.json.
        Produce un BackupAnalysisReport completo con hallazgos, recomendaciones y resumen.
        """
        threshold = threshold_repeated if threshold_repeated is not None else self.threshold_repeated
        analysis_id = f"analysis_{uuid.uuid4().hex[:10]}"
        resolved_dirs: List[Path] = [Path(d).resolve() for d in backup_dirs if Path(d).is_dir()]

        category_counter: Counter[str] = Counter()
        # path -> lista de registros con detalles
        path_failure_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        findings: List[BackupFinding] = []
        total_errors = 0

        for bdir in resolved_dirs:
            manifest, errors = self._read_backup_artifacts(bdir)

            # Analizar categorías en MANIFEST.json
            if manifest and "Categories" in manifest:
                for cat in manifest["Categories"]:
                    cat_id = cat.get("Id") or cat.get("id") or cat.get("Name") or cat.get("name") or "desconocida"
                    status = (cat.get("Status") or cat.get("status") or "UNKNOWN").upper()
                    name = cat.get("Name") or cat.get("name") or cat_id

                    if status in ("DENIED", "PARTIAL", "ERROR"):
                        category_counter[cat_id] += 1
                        total_errors += 1
                        path_or_query = cat.get("Directory") or cat.get("directory") or cat.get("Path") or cat.get("path") or name
                        path_failure_map[path_or_query].append({
                            "backup_dir": str(bdir),
                            "category": cat_id,
                            "status": status,
                            "message": f"Estado de categoría: {status}",
                        })

            # Analizar items en errors.json
            for err in errors:
                total_errors += 1
                if isinstance(err, dict):
                    err_name = (
                        err.get("Name")
                        or err.get("name")
                        or err.get("File")
                        or err.get("file")
                        or err.get("Path")
                        or err.get("path")
                        or "Desconocido"
                    )
                    err_status = (err.get("Status") or err.get("status") or err.get("Code") or err.get("code") or "ERROR").upper()
                    err_msg = err.get("Message") or err.get("message") or ""
                    cat = err.get("Category") or err.get("category") or "general"
                    category_counter[cat] += 1
                    path_failure_map[err_name].append({
                        "backup_dir": str(bdir),
                        "category": cat,
                        "status": err_status,
                        "message": err_msg,
                    })
                elif isinstance(err, str):
                    category_counter["general"] += 1
                    path_failure_map[err].append({
                        "backup_dir": str(bdir),
                        "category": "general",
                        "status": "ERROR",
                        "message": err,
                    })

        # Identificar rutas repetidamente denegadas o fallidas
        repeated_denied: List[str] = [
            path for path, failures in path_failure_map.items()
            if len(failures) >= threshold
        ]

        # Construir lista de hallazgos (findings)
        for path, failures in path_failure_map.items():
            fail_count = len(failures)
            sample = failures[0]
            cat = sample.get("category", "general")
            status = sample.get("status", "ERROR")
            msg = sample.get("message", "")

            sev = "ERROR" if fail_count >= threshold or status == "DENIED" else "WARNING"
            description = (
                f"La ruta o consulta '{path}' falló en {fail_count} respaldo(s) "
                f"con estado {status}. Detalle: {msg}"
            )
            findings.append(
                BackupFinding(
                    category=cat,
                    severity=sev,
                    error_code=status,
                    path_or_query=path,
                    failure_count=fail_count,
                    description=description,
                )
            )

        if not findings:
            findings.append(
                BackupFinding(
                    category="general",
                    severity="INFO",
                    error_code="OK",
                    path_or_query="N/A",
                    failure_count=0,
                    description="Todos los respaldos analizados están íntegros y saludables sin anomalías.",
                )
            )

        # Generar recomendaciones deterministas
        recommendations = self._generate_recommendations(
            repeated_denied,
            dict(category_counter),
            path_failure_map,
        )

        # Intentar resumen narrativo con LLM
        summary_text = self._generate_llm_summary(
            total_backups=len(resolved_dirs),
            total_errors=total_errors,
            category_errors=dict(category_counter),
            repeated_denied=repeated_denied,
            findings_count=len(findings),
            recommendations=recommendations,
        )

        model_used = self.model if summary_text else None

        # Fallback a resumen determinista estructurado
        if not summary_text:
            cats_text = ", ".join(f"{k} ({v})" for k, v in category_counter.items()) or "Ninguna"
            summary_text = (
                f"Se analizaron {len(resolved_dirs)} respaldos detectando un total de {total_errors} fallos. "
                f"Categorías con afectación: {cats_text}. "
                f"Se identificaron {len(repeated_denied)} rutas con fallos persistentes. "
                f"Se generaron {len(recommendations)} recomendaciones de ajuste."
            )

        return BackupAnalysisReport(
            analysis_id=analysis_id,
            backup_directories=[str(d) for d in resolved_dirs],
            total_backups_analyzed=len(resolved_dirs),
            total_errors=total_errors,
            errors_by_category=dict(category_counter),
            repeated_denied_paths=repeated_denied,
            findings=findings,
            recommendations=recommendations,
            summary_text=summary_text,
            model_used=model_used,
        )
