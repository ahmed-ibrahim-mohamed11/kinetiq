from edge_ai.camera.onvif_discovery import build_probe_message, parse_probe_match

PROBE_MATCH = b"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
  xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
  xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
  xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <SOAP-ENV:Header>
    <wsa:MessageID>urn:uuid:1</wsa:MessageID>
    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</wsa:Action>
  </SOAP-ENV:Header>
  <SOAP-ENV:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <wsa:EndpointReference><wsa:Address>urn:uuid:2419d68a-2dd2-21b2-a205-ec:8c:d2:30:11:af</wsa:Address></wsa:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>
        <d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/Profile/Streaming onvif://www.onvif.org/hardware/DS-2CD2143G2-I onvif://www.onvif.org/name/HIKVISION%20DS-2CD2143G2-I onvif://www.onvif.org/location/city/hangzhou</d:Scopes>
        <d:XAddrs>http://192.168.10.21/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
        <d:MetadataVersion>10</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""


def test_probe_message_is_ws_discovery_probe():
    msg = build_probe_message("urn:uuid:test").decode()
    assert "discovery/Probe</a:Action>" in msg
    assert "<a:MessageID>urn:uuid:test</a:MessageID>" in msg
    assert "dn:NetworkVideoTransmitter" in msg


def test_parse_probe_match_extracts_device_fields():
    devices = parse_probe_match(PROBE_MATCH, "192.168.10.21")
    assert len(devices) == 1
    d = devices[0]
    assert d.address == "192.168.10.21"
    assert d.xaddrs[0] == "http://192.168.10.21/onvif/device_service"
    assert d.model == "DS-2CD2143G2-I"
    assert d.name == "HIKVISION DS-2CD2143G2-I"
    assert d.supports_streaming_profile
    assert d.metadata_version == "10"


def test_parse_probe_match_rejects_garbage():
    assert parse_probe_match(b"not xml", "1.2.3.4") == []
    assert parse_probe_match(b"<a/>", "1.2.3.4") == []
