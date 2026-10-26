"""Meshtastic channels for the PortableTAK dashboard, run inside the OpenTAKServer app
context by users_worker. OpenTAKServer decrypts radio traffic with each channel's key and
reads its channel list when it starts, so changes apply after a server restart.

A channel is added from the share link the Meshtastic app shows (Channels > share:
https://meshtastic.org/e/#...), stored the way OpenTAKServer's own API stores it.
"""
import base64

DEFAULT_KEY = b'\x01'   # Meshtastic's well-known default key ("AQ==")


def _fail(error):
    return {'ok': False, 'error': error}


def _json(channel):
    return {'id': channel.id, 'name': channel.name or 'LongFast', 'url': channel.url,
            'uplink': bool(channel.uplink_enabled), 'downlink': bool(channel.downlink_enabled),
            'default_key': channel.psk in ('AQ==', None, ''), 'encrypted': channel.psk not in ('', None)}


def _store(db, MeshtasticChannel, channel_set):
    from meshtastic import apponly_pb2
    added = []
    for settings in channel_set.settings:
        single = apponly_pb2.ChannelSet()
        single.lora_config.CopyFrom(channel_set.lora_config)
        single.settings.append(settings)
        url = 'https://meshtastic.org/e/#' + base64.urlsafe_b64encode(single.SerializeToString()).decode()
        if db.session.query(MeshtasticChannel).filter_by(url=url).first():
            continue
        channel = MeshtasticChannel()
        channel.psk = base64.urlsafe_b64encode(settings.psk).decode('ascii')
        channel.name = settings.name or 'LongFast'
        # PortableTAK passes radio traffic both ways on every channel added here.
        channel.uplink_enabled, channel.downlink_enabled = True, True
        channel.position_precision = settings.module_settings.position_precision
        lora = channel_set.lora_config
        channel.lora_region, channel.lora_hop_limit = lora.region, lora.hop_limit or 3
        channel.lora_tx_enabled, channel.lora_tx_power = lora.tx_enabled, lora.tx_power
        channel.lora_sx126x_rx_boosted_gain, channel.modem_preset = lora.sx126x_rx_boosted_gain, lora.modem_preset
        channel.url = url
        db.session.add(channel)
        added.append(channel)
    db.session.commit()
    return added


def handle(app, db, request):
    from meshtastic import apponly_pb2, channel_pb2
    from opentakserver.models.Meshtastic import MeshtasticChannel
    action = request.get('action', '')

    if action == 'meshtastic_channels':
        rows = db.session.query(MeshtasticChannel).order_by(MeshtasticChannel.id).all()
        return {'ok': True, 'channels': [_json(c) for c in rows]}

    if action == 'meshtastic_add':
        link = str(request.get('url') or '').strip()
        if '#' not in link:
            return _fail('paste_the_channel_link')
        try:
            channel_set = apponly_pb2.ChannelSet()
            channel_set.ParseFromString(base64.urlsafe_b64decode(link.split('#')[-1] + '=='))
        except Exception:
            return _fail('not_a_meshtastic_link')
        if not channel_set.settings:
            return _fail('not_a_meshtastic_link')
        added = _store(db, MeshtasticChannel, channel_set)
        return {'ok': True, 'added': [_json(c) for c in added]}

    if action == 'meshtastic_default':
        # The standard public channel (LongFast, default key), when there are no channels.
        if db.session.query(MeshtasticChannel).count():
            return {'ok': True, 'added': []}
        channel_set = apponly_pb2.ChannelSet()
        settings = channel_pb2.ChannelSettings()
        settings.psk, settings.name = DEFAULT_KEY, ''
        channel_set.settings.append(settings)
        channel_set.lora_config.use_preset = True
        added = _store(db, MeshtasticChannel, channel_set)
        return {'ok': True, 'added': [_json(c) for c in added]}

    if action == 'meshtastic_delete':
        channel = db.session.query(MeshtasticChannel).filter_by(id=int(request.get('id') or 0)).first()
        if channel is None:
            return _fail('not_found')
        db.session.delete(channel)
        db.session.commit()
        return {'ok': True}

    return _fail('invalid_action')
