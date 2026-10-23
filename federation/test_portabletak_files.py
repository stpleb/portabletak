from opentakserver.federation import files

SHA = "b" * 64
COT = ('<event uid="u"><detail><fileshare filename="route.zip" name="Route" sha256="%s" sizeInBytes="1234" '
       'senderUid="ANDROID-1" senderCallsign="ALPHA" senderUrl="https://10.0.0.5:8443/Marti/sync/content?hash=%s"/>'
       '<marti><dest callsign="BRAVO"/></marti></detail></event>') % (SHA, SHA)


def test_fileshare_info():
    info = files.fileshare_info(COT)
    assert info["sha256"] == SHA and info["filename"] == "route.zip" and info["size"] == 1234
    assert info["sender_uid"] == "ANDROID-1"
    assert files.fileshare_info('<event uid="p"><point/></event>') is None


def test_hash_from_url_when_attribute_missing():
    cot = COT.replace('sha256="%s" ' % SHA, "")
    assert files.fileshare_info(cot)["sha256"] == SHA


def test_rewrite_points_at_this_server():
    out = files.rewrite_sender_url(COT, SHA, 8443)
    assert "https://%s:8443/Marti/sync/content?hash=%s" % (files.PLACEHOLDER_HOST, SHA) in out
    assert "10.0.0.5" not in out and '<dest callsign="BRAVO"/>' in out
