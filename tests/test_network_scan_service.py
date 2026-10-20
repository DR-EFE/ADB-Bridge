"""Pruebas del escáner Nmap sin ejecutar tráfico de red real."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.services.network_scan_service import (
    NetworkHost,
    NetworkScanResult,
    NetworkScanService,
    PortScanResult,
    ScanProfile,
)


NMAP_XML = """<?xml version='1.0'?>
<nmaprun>
  <host>
    <status state='up'/>
    <address addr='192.168.1.42' addrtype='ipv4'/>
    <address addr='AA:BB:CC:DD:EE:FF' addrtype='mac' vendor='Example Mobile'/>
    <hostnames><hostname name='android-office'/></hostnames>
    <times srtt='12500'/>
    <ports>
      <port protocol='tcp' portid='5555'><state state='open' reason='syn-ack'/><service name='adb'/></port>
      <port protocol='tcp' portid='4444'><state state='filtered' reason='no-response'/></port>
    </ports>
  </host>
</nmaprun>"""


class TestNetworkScanService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = NetworkScanService(history_path=Path(self.temp_dir.name) / "network-scan-history.json")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_nmap_xml_reads_identity_ports_and_latency(self) -> None:
        hosts = self.service.parse_nmap_xml(NMAP_XML)
        self.assertEqual(1, len(hosts))
        host = hosts[0]
        self.assertEqual("192.168.1.42", host.ip)
        self.assertEqual("AABBCCDDEEFF", host.mac)
        self.assertEqual("Example Mobile", host.vendor)
        self.assertEqual("android-office", host.hostname)
        self.assertEqual(12.5, host.latency_ms)
        self.assertEqual([5555], host.adb_ports)
        self.assertEqual("filtered", host.ports[1].state)

    def test_exact_mac_match_is_only_automatic_match(self) -> None:
        host = NetworkHost(ip="192.168.1.42", mac="AABBCCDDEEFF", ports=[PortScanResult(5555, state="open")])
        self.service.match_inventory([host], [{
            "Id": "phone-1", "FriendlyName": "Telefono oficina", "WifiMacAddress": "aa:bb:cc:dd:ee:ff",
            "LastEndpoint": "192.168.1.22:5555",
        }])
        self.assertEqual("phone-1", host.known_device_id)
        self.assertEqual("mac", host.match_type)
        self.assertTrue(host.auto_connect)

        ip_only = NetworkHost(ip="192.168.1.22", ports=[PortScanResult(5555, state="open")])
        self.service.match_inventory([ip_only], [{"Id": "phone-1", "FriendlyName": "Telefono oficina", "LastIp": "192.168.1.22"}])
        self.assertEqual("ip", ip_only.match_type)
        self.assertFalse(ip_only.auto_connect)

    def test_mdns_is_merged_without_promoting_an_untrusted_match(self) -> None:
        hosts = []
        self.service.merge_mdns(hosts, [{"host": "192.168.1.50", "port": "39111", "service_name": "_adb-tls-connect._tcp"}])
        self.assertEqual("192.168.1.50", hosts[0].ip)
        self.assertEqual([39111], hosts[0].adb_ports)
        self.service.match_inventory(hosts, [{"Id": "phone", "FriendlyName": "Wireless", "MdnsServiceName": "_adb-tls-connect._tcp"}])
        self.assertEqual("mdns", hosts[0].match_type)
        self.assertFalse(hosts[0].auto_connect)

    def test_history_reports_ip_and_port_changes(self) -> None:
        first = NetworkScanResult(profile="quick", target="192.168.1.0/24", started_utc="2026-01-01T00:00:00Z", hosts=[
            NetworkHost(ip="192.168.1.10", mac="AABBCCDDEEFF", ports=[PortScanResult(5555, state="open")])
        ])
        self.assertIn("Nuevo:", self.service.compare_and_save(first)[0])
        second_host = NetworkHost(ip="192.168.1.11", mac="AABBCCDDEEFF", ports=[PortScanResult(40001, state="open")])
        second = NetworkScanResult(profile="quick", target="192.168.1.0/24", started_utc="2026-01-02T00:00:00Z", hosts=[second_host])
        changes = self.service.compare_and_save(second)
        self.assertTrue(any("IP cambió" in item for item in changes))
        self.assertTrue(any("Puertos cambiaron" in item for item in changes))

    @patch.object(NetworkScanService, "resolve_nmap", return_value="C:/tools/nmap.exe")
    def test_known_profile_uses_each_saved_ip_as_its_own_argument(self, _mock_nmap) -> None:
        command = self.service.build_command(
            ScanProfile.KNOWN,
            "192.168.1.0/24",
            [{"LastEndpoint": "192.168.1.20:40123"}, {"LastIp": "192.168.1.21"}],
        )
        self.assertIn("-Pn", command)
        self.assertEqual(["192.168.1.20", "192.168.1.21"], command[-2:])
        self.assertIn("40123", command[command.index("-p") + 1])


if __name__ == "__main__":
    unittest.main()
