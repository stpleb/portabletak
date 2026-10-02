#!/usr/bin/env python3
"""Submit the first-run values to a PortableTAK host through an SSH tunnel.

Open the tunnel first (the API only listens on the host's loopback):

    ssh -N -L 8090:127.0.0.1:8090 root@<host>

Then run this on the Mac. Passwords are read without echo, never printed, and
sent only to the tunnelled localhost API. The request is validated first
(/api/provision/validate, which activates nothing) and submitted only after
you confirm.

For disposable test cartridges only:
    --from-file PATH   read the four values from a 0600 JSON file and submit
                       without prompting
    --api URL          provision endpoint (default http://127.0.0.1:8090/api/provision)
"""
import argparse
import getpass
import json
import os
import stat
import sys
import urllib.error
import urllib.request

API = 'http://127.0.0.1:8090/api/provision'
HEADERS = {'Content-Type': 'application/json', 'X-PortableTAK-Setup': '1'}


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, headers=HEADERS,
                                     method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b'{}')
        except ValueError:
            return error.code, {}


def secret(label):
    # Mirrors the server rule: 12-128 printable ASCII characters (space to ~).
    while True:
        first = getpass.getpass(f'{label} (12-128 characters): ')
        if not 8 <= len(first) <= 128:
            print(f'  Must be 12-128 characters; that was {len(first)}. Try again.')
            continue
        if any(not 32 <= ord(c) <= 126 for c in first):
            print('  Use only plain keyboard characters: letters, digits, space and')
            print('  standard symbols. No accented letters, Option-key symbols, curly')
            print('  quotes, or tabs. Try again.')
            continue
        if getpass.getpass(f'{label} again: ') == first:
            return first
        print('  Did not match, try again.')


def load_request(path):
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit('Credential file must be private (chmod 600).')
    with open(path) as stream:
        request = json.load(stream)
    required = {'instance_name', 'administrator_password', 'client_password'}
    if not required <= set(request) <= required | {'timezone'}:
        raise SystemExit('Credential file needs instance_name and the two passwords (timezone optional).')
    return request


def main():
    global API
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-file')
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    API = args.api
    try:
        status, current = call('/status')
    except urllib.error.URLError:
        print('Cannot reach 127.0.0.1:8090. Is the SSH tunnel open?')
        return 1
    print(f"Cartridge {current.get('cartridge_id')} state={current.get('state')}")
    if current.get('state') not in {'new', 'provisioning', 'failed'}:
        print('This cartridge is not waiting for first-run setup. Nothing sent.')
        return 1
    if args.from_file:
        request = load_request(args.from_file)
        status, result = call('/validate', request)
        if status != 200:
            print('Validation refused:', result.get('detail', status), '- nothing was activated.')
            return 1
        status, result = call('', request)
        request.clear()
        print('Provisioning started in the background.' if status == 200
              else f"Provisioning was not started: {result.get('detail', status)}")
        return 0 if status == 200 else 1
    request = {
        'instance_name': input('Server name (letters, digits, space . _ -): ').strip(),
        'administrator_password': secret('Administrator password (also the dashboard login)'),
        'client_password': secret('Client/truststore password (different from the administrator)'),
    }
    status, result = call('/validate', request)
    if status != 200:
        print('Validation refused:', result.get('detail', status), '- nothing was activated.')
        return 1
    print('Validated. Nothing has been activated yet.')
    while True:
        answer = input('Type PROVISION to start first-run setup (or cancel): ').strip()
        if answer == 'PROVISION':
            break
        if answer.lower() in {'cancel', 'no', 'n', 'q', 'quit'}:
            print('Cancelled. Nothing was activated.')
            return 1
        print(f'  "{answer}" is not PROVISION. Try again, or type cancel.')
    status, result = call('', request)
    request.clear()
    if status != 200:
        print('Provisioning was not started:', result.get('detail', status))
        return 1
    print('Provisioning started in the background. Claude can follow the journal from here.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
