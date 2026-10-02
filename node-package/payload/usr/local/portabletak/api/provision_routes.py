"""Candidate API routes. Add with app.include_router(router).

Full activation deliberately remains unavailable until bootstrap is validated.
"""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from portabletak_provision import Controller, MAX_INPUT, ProvisionError, validate_input
from release import expected_base_seal
from bootstrap_job import BootstrapJobSupervisor

router = APIRouter(prefix='/api/provision')
controller = Controller(expected_base_seal=expected_base_seal())
supervisor = BootstrapJobSupervisor()

def activation_enabled():
    return Path('/usr/local/portabletak/release/first-run-enabled').is_file()

async def read_input(request):
    host = request.headers.get('host', '')
    try:
        hostname = urlsplit('http://' + host).hostname
    except ValueError:
        raise HTTPException(403, 'invalid_management_host')
    if hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise HTTPException(403, 'invalid_management_host')
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{host}':
        raise HTTPException(403, 'same_origin_required')
    if request.headers.get('x-portabletak-setup') != '1':
        raise HTTPException(403, 'setup_header_required')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'json_required')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_INPUT:
            raise HTTPException(413, 'input_too_large')
    try:
        data = json.loads(raw)
        return validate_input(data, controller.timezone_root())
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'invalid_json') from None
    except ProvisionError as error:
        raise HTTPException(400, error.code) from None

@router.get('/status')
def provision_status():
    status = controller.status()
    try:
        status['bootstrap_job'] = supervisor.status()
    except ProvisionError as error:
        status['bootstrap_job'] = {'ok': False, 'job': 'unavailable', 'error': error.code}
    return status

@router.post('/validate')
async def provision_validate(request: Request):
    await read_input(request)
    return {'ok': True, 'validated': True, 'activated': False}

@router.post('')
async def provision(request: Request):
    payload = await read_input(request)
    status = controller.status()
    if status.get('complete') or status.get('state') == 'recovery':
        raise HTTPException(409, 'cartridge_already_initialized')
    if not status.get('ok') or status.get('state') not in {'new', 'provisioning', 'failed'}:
        raise HTTPException(409, status.get('error', 'cartridge_not_ready'))
    if not activation_enabled():
        raise HTTPException(409, 'runtime_backend_not_enabled')
    try:
        return supervisor.launch(payload)
    except ProvisionError as error:
        raise HTTPException(409, error.code) from None
