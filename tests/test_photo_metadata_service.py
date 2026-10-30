"""Pruebas unitarias para PhotoMetadataService y MetadataPage."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.domain.models.photo_metadata import (
    EvidenceArtifact,
    EvidenceManifest,
    MetadataAnalysisSummary,
    MetadataCluster,
    MetadataSessionResult,
    PhotoMetadataRecord,
)
from core.services.photo_metadata_service import PhotoMetadataService


def test_photo_metadata_record_from_dict():
    data = {
        "File": "source/IMG_001.jpg",
        "FileName": "IMG_001.jpg",
        "SourceFile": "C:/tmp/IMG_001.jpg",
        "DateTimeOriginal": "2023:08:15 14:32:01",
        "Make": "Samsung",
        "Model": "SM-G998B",
        "Lat": 19.432608,
        "Lon": -99.133209,
        "Altitude": 2240.5,
        "HasGPS": True,
        "Orientation": 1,
        "Software": "Adobe Lightroom",
        "ISO": "100",
        "FNumber": "1.8",
        "ExposureTime": "1/250",
        "ImageWidth": 4000,
        "ImageHeight": 3000,
        "Thumbnail": "thumbs/thumb_1.jpg",
        "GoogleMapsUrl": "https://maps.google.com/?q=19.432608,-99.133209",
        "Sha256": "ABCDEF123456",
        "FileSizeBytes": 2048000,
    }
    rec = PhotoMetadataRecord.from_dict(data)
    assert rec.file_name == "IMG_001.jpg"
    assert rec.make == "Samsung"
    assert rec.has_gps is True
    assert rec.lat == 19.432608
    assert rec.lon == -99.133209
    assert rec.altitude == 2240.5
    assert rec.orientation == 1
    assert rec.software == "Adobe Lightroom"


def test_metadata_summary_and_cluster_from_dict():
    summary_data = {
        "TotalImages": 10,
        "GpsImages": 8,
        "NoGpsImages": 2,
        "GpsCoveragePercent": 80.0,
        "DateRange": {"Min": "2023:01:01", "Max": "2023:12:31"},
        "Devices": [{"Device": "Google Pixel 7", "Count": 8}, {"Device": "Desconocido", "Count": 2}],
        "TopClusters": [
            {"Lat": 19.432, "Lon": -99.133, "Count": 5, "GoogleMapsUrl": "https://maps.google.com/?q=19.432,-99.133"},
            {"Lat": 20.673, "Lon": -103.344, "Count": 3, "GoogleMapsUrl": "https://maps.google.com/?q=20.673,-103.344"},
        ],
        "SoftwareDetected": [{"Software": "Snapseed", "Count": 2}],
        "EditedCount": 2,
        "StrippedCount": 1,
        "AnalyzedAt": "2026-10-29 18:00:00",
    }
    summary = MetadataAnalysisSummary.from_dict(summary_data)
    assert summary.total_images == 10
    assert summary.gps_images == 8
    assert summary.no_gps_images == 2
    assert summary.gps_coverage_percent == 80.0
    assert summary.date_min == "2023:01:01"
    assert summary.date_max == "2023:12:31"
    assert len(summary.top_clusters) == 2
    assert summary.top_clusters[0].count == 5
    assert summary.top_clusters[0].lat == 19.432


def test_evidence_manifest_and_verification(tmp_path):
    session_dir = tmp_path / "Metadata_20261029_120000"
    session_dir.mkdir()

    # Crear dos archivos en la sesión
    art1 = session_dir / "normalized_metadata.json"
    art1.write_text("{}", encoding="utf-8")
    import hashlib
    h1 = hashlib.sha256(b"{}").hexdigest().upper()

    manifest_data = {
        "SessionId": "Metadata_20261029_120000",
        "AcquisitionTime": "2026-10-29 12:00:00",
        "HostName": "DESK-TEST",
        "User": "Analyst",
        "DeviceSerial": "SERIAL123",
        "SourceType": "Folder",
        "SourceOrigin": "C:/Photos",
        "TotalImages": 5,
        "Artifacts": [
            {"FileName": "normalized_metadata.json", "SizeBytes": len(b"{}"), "Sha256": h1},
            {"FileName": "tampered_artifact.csv", "SizeBytes": 10, "Sha256": "FFFFFFFFFFFF"},
        ],
    }

    manifest_path = session_dir / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    service = PhotoMetadataService(base_backup_dir=tmp_path)
    res = service.verify_manifest_integrity(session_dir)

    assert "normalized_metadata.json" in res
    assert res["normalized_metadata.json"]["valid"] is True
    assert "tampered_artifact.csv" in res
    assert res["tampered_artifact.csv"]["valid"] is False


def test_list_previous_sessions(tmp_path):
    s1 = tmp_path / "Metadata_20261029_100000"
    s1.mkdir()
    (s1 / "analysis_summary.json").write_text(json.dumps({"TotalImages": 4, "GpsImages": 2}), encoding="utf-8")

    s2 = tmp_path / "Metadata_20261029_110000"
    s2.mkdir()
    (s2 / "evidence_manifest.json").write_text(json.dumps({"SourceType": "Archive", "TotalImages": 12}), encoding="utf-8")

    not_a_session = tmp_path / "OtherFolder"
    not_a_session.mkdir()

    service = PhotoMetadataService(base_backup_dir=tmp_path)
    sessions = service.list_previous_sessions()

    assert len(sessions) == 2
    ids = [s["session_id"] for s in sessions]
    assert "Metadata_20261029_100000" in ids
    assert "Metadata_20261029_110000" in ids


def test_metadata_page_initialization(tmp_path):
    from unittest.mock import MagicMock
    from ui.pages.metadata_page import MetadataPage, scan_pc_photo_directories
    from ui.services.device_service import DeviceService

    device = DeviceService(serial="TEST_SERIAL_999")
    mock_page = MagicMock()
    mock_page.services = []
    mock_page.overlay = []
    meta_page = MetadataPage(device=device, page=mock_page)

    assert len(mock_page.overlay) == 0
    assert len(mock_page.services) == 3
    assert meta_page.view is not None
    assert meta_page._input_mode.value == "folder"
    assert meta_page._kpi_total.value == "0"
    assert meta_page._btn_open_map.disabled is True

    # Verificar actualización de banner de previsualización al seleccionar carpeta en PC
    sample_dir = tmp_path / "MyPhotos"
    sample_dir.mkdir()
    (sample_dir / "pic1.jpg").write_bytes(b"dummy")
    meta_page._update_selection_preview(str(sample_dir))
    assert meta_page._preview_info_banner.visible is True
    assert "1 imágenes detectadas" in meta_page._preview_info_banner.content.controls[1].value
