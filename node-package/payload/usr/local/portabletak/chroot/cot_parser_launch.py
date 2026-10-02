"""Start the upstream OTS CoT parser with RabbitMQ credentials on Socket.IO.

OpenTAKServer 1.7.13's cot_parser builds its Socket.IO message queue as
"amqp://" + address with no credentials, so kombu falls back to RabbitMQ's
guest account, which PortableTAK cartridges do not have. The clean-base build
patches the same line in app.py but missed this one. This launcher adds the
cartridge's configured credentials in memory and leaves the sealed package
untouched. Remove it once the rebuilt clean base carries the patch.

Usage (inside the cartridge chroot):
    python cot_parser_launch.py /asgard/opentak/venv/bin/cot_parser [args...]
The upstream entry-point path is passed so process checks still recognise it.
"""
import json
import os
import sys
from urllib.parse import quote


def credentials(config_path):
    try:
        with open(config_path) as stream:
            settings = json.load(stream)
    except (OSError, ValueError):
        return None
    user = settings.get('OTS_RABBITMQ_USERNAME')
    password = settings.get('OTS_RABBITMQ_PASSWORD')
    if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
        return None
    return user, password


def with_credentials(queue, pair):
    if not isinstance(queue, str) or not queue.startswith('amqp://') or '@' in queue or not pair:
        return queue
    user, password = pair
    return 'amqp://' + quote(user, safe='') + ':' + quote(password, safe='') + '@' + queue[len('amqp://'):]


def install(config_path, socketio_module):
    pair = credentials(config_path)
    if not pair:
        return False
    original = socketio_module.SocketIO.__init__

    def init(self, app=None, **kwargs):
        if 'message_queue' in kwargs:
            kwargs['message_queue'] = with_credentials(kwargs['message_queue'], pair)
        return original(self, app, **kwargs)

    socketio_module.SocketIO.__init__ = init
    return True


def main():
    if len(sys.argv) < 2:
        print('usage: cot_parser_launch.py ENTRY_POINT [args...]', file=sys.stderr)
        return 2
    import flask_socketio
    data = os.environ.get('OTS_DATA_FOLDER', '/asgard/opentak/data')
    install(os.path.join(data, 'config.yml'), flask_socketio)
    sys.argv = sys.argv[1:]
    from opentakserver.cot_parser.cot_parser import main as cot_parser_main
    return cot_parser_main()


if __name__ == '__main__':
    sys.exit(main())
