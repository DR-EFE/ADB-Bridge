"""Modelos de dominio para metadatos fotográficos, geolocalización y evidencia forense."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PhotoMetadataRecord:
    """Registro individual de metadatos de una fotografía."""
    file: str
    file_name: str
    source_file: str
    date_time_original: str = ""
    make: str = ""
    model: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude: Optional[float] = None
    has_gps: bool = False
    orientation: int = 1
    software: str = ""
    iso: str = ""
    f_number: str = ""
    exposure_time: str = ""
    image_width: int = 0
    image_height: int = 0
    thumbnail: str = ""
    thumb_rel: str = ""
    google_maps_url: str = ""
    sha256: str = ""
    file_size_bytes: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PhotoMetadataRecord:
        return cls(
            file=data.get("File", "") or data.get("file", ""),
            file_name=data.get("FileName", "") or data.get("file_name", ""),
            source_file=data.get("SourceFile", "") or data.get("source_file", ""),
            date_time_original=data.get("DateTimeOriginal", "") or data.get("date_time_original", ""),
            make=data.get("Make", "") or data.get("make", ""),
            model=data.get("Model", "") or data.get("model", ""),
            lat=data.get("Lat") if data.get("Lat") is not None else data.get("lat"),
            lon=data.get("Lon") if data.get("Lon") is not None else data.get("lon"),
            altitude=data.get("Altitude") if data.get("Altitude") is not None else data.get("altitude"),
            has_gps=bool(data.get("HasGPS", False) or data.get("has_gps", False)),
            orientation=int(data.get("Orientation", 1) or 1),
            software=data.get("Software", "") or data.get("software", ""),
            iso=str(data.get("ISO", "") or data.get("iso", "")),
            f_number=str(data.get("FNumber", "") or data.get("f_number", "")),
            exposure_time=str(data.get("ExposureTime", "") or data.get("exposure_time", "")),
            image_width=int(data.get("ImageWidth", 0) or 0),
            image_height=int(data.get("ImageHeight", 0) or 0),
            thumbnail=data.get("Thumbnail", "") or data.get("thumbnail", ""),
            thumb_rel=data.get("ThumbRel", "") or data.get("thumb_rel", ""),
            google_maps_url=data.get("GoogleMapsUrl", "") or data.get("google_maps_url", ""),
            sha256=data.get("Sha256", "") or data.get("sha256", ""),
            file_size_bytes=int(data.get("FileSizeBytes", 0) or 0),
        )


@dataclass
class MetadataCluster:
    """Cluster geográfico de fotografías agrupadas por proximidad (~110m)."""
    lat: float
    lon: float
    count: int
    google_maps_url: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MetadataCluster:
        return cls(
            lat=float(data.get("Lat", 0.0) or data.get("lat", 0.0)),
            lon=float(data.get("Lon", 0.0) or data.get("lon", 0.0)),
            count=int(data.get("Count", 0) or data.get("count", 0)),
            google_maps_url=data.get("GoogleMapsUrl", "") or data.get("google_maps_url", ""),
        )


@dataclass
class MetadataAnalysisSummary:
    """Resumen estadístico y forense de la colección analizada."""
    total_images: int = 0
    gps_images: int = 0
    no_gps_images: int = 0
    gps_coverage_percent: float = 0.0
    date_min: str = "N/A"
    date_max: str = "N/A"
    devices: List[Dict[str, Any]] = field(default_factory=list)
    top_clusters: List[MetadataCluster] = field(default_factory=list)
    software_detected: List[Dict[str, Any]] = field(default_factory=list)
    edited_count: int = 0
    stripped_count: int = 0
    analyzed_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MetadataAnalysisSummary:
        date_range = data.get("DateRange", {}) or data.get("date_range", {})
        raw_clusters = data.get("TopClusters", []) or data.get("top_clusters", [])
        clusters = [MetadataCluster.from_dict(c) for c in raw_clusters]

        return cls(
            total_images=int(data.get("TotalImages", 0) or 0),
            gps_images=int(data.get("GpsImages", 0) or 0),
            no_gps_images=int(data.get("NoGpsImages", 0) or 0),
            gps_coverage_percent=float(data.get("GpsCoveragePercent", 0.0) or 0.0),
            date_min=date_range.get("Min", "N/A") if isinstance(date_range, dict) else "N/A",
            date_max=date_range.get("Max", "N/A") if isinstance(date_range, dict) else "N/A",
            devices=data.get("Devices", []) or [],
            top_clusters=clusters,
            software_detected=data.get("SoftwareDetected", []) or [],
            edited_count=int(data.get("EditedCount", 0) or 0),
            stripped_count=int(data.get("StrippedCount", 0) or 0),
            analyzed_at=data.get("AnalyzedAt", "") or data.get("analyzed_at", ""),
        )


@dataclass
class EvidenceArtifact:
    """Artefacto de salida registrado con hash criptográfico SHA-256."""
    file_name: str
    size_bytes: int
    sha256: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceArtifact:
        return cls(
            file_name=data.get("FileName", "") or data.get("file_name", ""),
            size_bytes=int(data.get("SizeBytes", 0) or 0),
            sha256=data.get("Sha256", "") or data.get("sha256", ""),
        )


@dataclass
class EvidenceManifest:
    """Manifiesto de cadena de custodia para evidencia forense."""
    session_id: str = ""
    acquisition_time: str = ""
    host_name: str = ""
    user: str = ""
    device_serial: str = "N/A"
    source_type: str = "Unknown"
    source_origin: str = ""
    total_images: int = 0
    artifacts: List[EvidenceArtifact] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceManifest:
        raw_artifacts = data.get("Artifacts", []) or data.get("artifacts", [])
        arts = [EvidenceArtifact.from_dict(a) for a in raw_artifacts]
        return cls(
            session_id=data.get("SessionId", "") or data.get("session_id", ""),
            acquisition_time=data.get("AcquisitionTime", "") or data.get("acquisition_time", ""),
            host_name=data.get("HostName", "") or data.get("host_name", ""),
            user=data.get("User", "") or data.get("user", ""),
            device_serial=data.get("DeviceSerial", "N/A") or data.get("device_serial", "N/A"),
            source_type=data.get("SourceType", "Unknown") or data.get("source_type", "Unknown"),
            source_origin=data.get("SourceOrigin", "") or data.get("source_origin", ""),
            total_images=int(data.get("TotalImages", 0) or 0),
            artifacts=arts,
        )


@dataclass
class MetadataSessionResult:
    """Resultado consolidado de una sesión de análisis de metadatos."""
    session_dir: str
    manifest: EvidenceManifest
    summary: MetadataAnalysisSummary
    records: List[PhotoMetadataRecord] = field(default_factory=list)
    map_html_path: str = ""
    dashboard_html_path: str = ""
    raw_csv_path: str = ""

