"""
ONVIF device discovery (WS-Discovery) with the standard library only.

Sends a WS-Discovery Probe to the multicast group 239.255.255.250:3702 and
collects ProbeMatch responses. Returns the device service addresses (XAddrs)
and scopes (which usually include the vendor name, hardware model and the
`onvif://www.onvif.org/Profile/Streaming` capability). Getting the actual RTSP
URI then requires authenticated ONVIF Media calls (`get_stream_uri`, which uses
`onvif-zeep` when installed).
"""

from __future__ import annotations

import socket
import time
import uuid
import xml.etree.ElementTree as ET  # noqa: S405 — parsing device responses; defusedxml optional below
from dataclasses import dataclass, field

try:  # prefer a hardened parser when available
    from defusedxml import ElementTree as SafeET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    SafeET = ET

MULTICAST_GROUP = "239.255.255.250"
MULTICAST_PORT = 3702

_NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "dn": "http://www.onvif.org/ver10/network/wsdl",
}


@dataclass(frozen=True)
class OnvifDevice:
    address: str  # responder IP
    xaddrs: tuple[str, ...]  # device service endpoints, e.g. http://192.168.1.10/onvif/device_service
    scopes: tuple[str, ...]
    endpoint_reference: str | None = None
    metadata_version: str | None = None

    @property
    def model(self) -> str | None:
        return _scope_value(self.scopes, "hardware") or _scope_value(self.scopes, "model")

    @property
    def name(self) -> str | None:
        return _scope_value(self.scopes, "name")

    @property
    def supports_streaming_profile(self) -> bool:
        return any("Profile/Streaming" in s or "Profile/S" in s or "Profile/T" in s for s in self.scopes)


def _scope_value(scopes: tuple[str, ...], key: str) -> str | None:
    prefix = f"onvif://www.onvif.org/{key}/"
    for s in scopes:
        if s.startswith(prefix):
            from urllib.parse import unquote

            return unquote(s[len(prefix) :])
    return None


def build_probe_message(message_id: str | None = None) -> bytes:
    mid = message_id or f"urn:uuid:{uuid.uuid4()}"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<s:Header>"
        '<a:Action s:mustUnderstand="1">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>'
        f"<a:MessageID>{mid}</a:MessageID>"
        "<a:ReplyTo><a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address></a:ReplyTo>"
        '<a:To s:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>'
        "</s:Header>"
        "<s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body>"
        "</s:Envelope>"
    )
    return xml.encode("utf-8")


def parse_probe_match(payload: bytes, address: str) -> list[OnvifDevice]:
    """Parse a ProbeMatches SOAP message into devices. Malformed input yields []."""
    try:
        root = SafeET.fromstring(payload)  # noqa: S314 — see SafeET above
    except ET.ParseError:
        return []
    devices: list[OnvifDevice] = []
    for match in root.iterfind(".//d:ProbeMatch", _NS):
        xaddrs_el = match.find("d:XAddrs", _NS)
        scopes_el = match.find("d:Scopes", _NS)
        epr_el = match.find("a:EndpointReference/a:Address", _NS)
        mdv_el = match.find("d:MetadataVersion", _NS)
        xaddrs = tuple((xaddrs_el.text or "").split()) if xaddrs_el is not None else ()
        scopes = tuple((scopes_el.text or "").split()) if scopes_el is not None else ()
        devices.append(
            OnvifDevice(
                address=address,
                xaddrs=xaddrs,
                scopes=scopes,
                endpoint_reference=(epr_el.text or None) if epr_el is not None else None,
                metadata_version=(mdv_el.text or None) if mdv_el is not None else None,
            )
        )
    return devices


@dataclass
class DiscoveryResult:
    devices: list[OnvifDevice] = field(default_factory=list)
    raw_responses: int = 0
    duration_s: float = 0.0


def discover(timeout_s: float = 3.0, interface_ip: str | None = None) -> DiscoveryResult:
    """Broadcast a Probe and collect answers for `timeout_s`. Safe on networks without ONVIF devices."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    if interface_ip:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
    sock.bind((interface_ip or "0.0.0.0", 0))  # noqa: S104 — ephemeral local port for replies
    sock.settimeout(0.5)
    result = DiscoveryResult()
    start = time.monotonic()
    seen: set[tuple[str, tuple[str, ...]]] = set()
    try:
        sock.sendto(build_probe_message(), (MULTICAST_GROUP, MULTICAST_PORT))
        while time.monotonic() - start < timeout_s:
            try:
                data, (addr, _port) = sock.recvfrom(65535)
            except TimeoutError:
                continue
            result.raw_responses += 1
            for dev in parse_probe_match(data, addr):
                key = (dev.address, dev.xaddrs)
                if key not in seen:
                    seen.add(key)
                    result.devices.append(dev)
    finally:
        sock.close()
    result.duration_s = round(time.monotonic() - start, 3)
    return result


def get_stream_uri(xaddr: str, username: str, password: str, profile_index: int = 0) -> str:
    """Resolve the RTSP URI of a media profile via ONVIF Media service (requires `onvif-zeep`)."""
    from urllib.parse import urlparse

    try:
        from onvif import ONVIFCamera  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("install the 'cv' extra (onvif-zeep) to resolve stream URIs") from exc
    parsed = urlparse(xaddr)
    cam = ONVIFCamera(parsed.hostname, parsed.port or 80, username, password)
    media = cam.create_media_service()
    profiles = media.GetProfiles()
    token = profiles[profile_index].token
    req = media.create_type("GetStreamUri")
    req.ProfileToken = token
    req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
    return str(media.GetStreamUri(req).Uri)
