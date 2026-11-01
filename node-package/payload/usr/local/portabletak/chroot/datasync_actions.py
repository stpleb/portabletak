"""Data Sync feeds for the PortableTAK dashboard, run inside the OpenTAKServer app context
by users_worker.

A feed is an OpenTAKServer mission. ATAK's trash cans only clear the phone's copy, and its
server delete needs the mission owner's role, so the dashboard can remove any feed. Phones
are told it is gone, and the feed's uploaded files stay on the server (they may be in other
feeds). A federated server hears of the delete from the federation sync.
"""
import datetime
import json
from xml.etree.ElementTree import tostring


def _fail(error):
    return {'ok': False, 'error': error}


def _json(mission):
    made = mission.create_time
    owner = mission.owner
    return {'name': mission.name,
            'creator': (owner.callsign if owner else None) or mission.creator_uid,
            'time': made.replace(tzinfo=datetime.timezone.utc).isoformat() if made else None,
            'items': len(mission.contents) + len(mission.uids),
            'subscribers': len(mission.roles),
            'tool': mission.tool, 'password': bool(mission.password_protected)}


def delete_notice(mission, generate):
    """The CoT that drops the feed from phones, or None: a feed with no tool or guid cannot be
    serialized, and it must still be deletable."""
    try:
        return tostring(generate(mission)).decode('utf-8')
    except (TypeError, ValueError):
        return None


def _publish(app, cot):
    import pika
    credentials = pika.PlainCredentials(app.config.get('OTS_RABBITMQ_USERNAME', 'guest'),
                                        app.config.get('OTS_RABBITMQ_PASSWORD', 'guest'))
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=app.config.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.2'), credentials=credentials))
    try:
        connection.channel().basic_publish(exchange='missions', routing_key='missions',
                                           body=json.dumps({'uid': app.config.get('OTS_NODE_ID'), 'cot': cot}))
    finally:
        connection.close()


def handle(app, db, request):
    from sqlalchemy import delete
    from opentakserver.blueprints.marti_api.mission_marti_api import generate_mission_delete_cot
    from opentakserver.models.Chatrooms import Chatroom
    from opentakserver.models.ChatroomsUids import ChatroomsUids
    from opentakserver.models.GeoChat import GeoChat
    from opentakserver.models.GroupMission import GroupMission
    from opentakserver.models.Mission import Mission
    from opentakserver.models.MissionContentMission import MissionContentMission
    action = request.get('action', '')

    if action == 'ds_list':
        rows = db.session.query(Mission).order_by(Mission.name).all()
        return {'ok': True, 'feeds': [_json(m) for m in rows]}

    if action == 'ds_delete':
        name = str(request.get('name') or '')
        mission = db.session.query(Mission).filter_by(name=name).first()
        if mission is None:
            return _fail('not_found')
        notice = delete_notice(mission, generate_mission_delete_cot)
        # As in the phones' own delete: these two links do not cascade. Items placed on the
        # feed lose their link to it, and its log, roles and changes go with it.
        db.session.execute(delete(GroupMission).where(GroupMission.mission_name == name))
        db.session.execute(delete(MissionContentMission).where(MissionContentMission.mission_name == name))
        room = db.session.query(Chatroom).filter_by(name=name).first()
        if room is not None:
            db.session.execute(delete(ChatroomsUids).where(ChatroomsUids.chatroom_id == room.id))
            db.session.execute(delete(GeoChat).where(GeoChat.chatroom_id == room.id))
            db.session.delete(room)
        db.session.delete(mission)
        db.session.commit()
        told = False
        if notice:
            try:
                _publish(app, notice)
                told = True
            except Exception:
                pass   # the feed is gone; phones drop it on their next sync
        return {'ok': True, 'phones_told': told}

    return _fail('unknown_action')
