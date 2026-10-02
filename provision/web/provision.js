let provisionState = null;
let dashboardStarted = false;
let provisioningPoll = null;
const setupElement = id => document.getElementById(id);

function setupMessage(message) {
    setupElement('provisionMessage').textContent = message;
}

function clearSetupPasswords() {
    for (const id of ['setupAdminPassword', 'setupAdminConfirm', 'setupClientPassword', 'setupClientConfirm']) {
        setupElement(id).value = '';
    }
}

function setupPayload() {
    if (!setupElement('provisionForm').reportValidity()) return null;
    if (setupElement('setupAdminPassword').value !== setupElement('setupAdminConfirm').value ||
        setupElement('setupClientPassword').value !== setupElement('setupClientConfirm').value) {
        setupMessage('The confirmation passwords must match.');
        return null;
    }
    return {
        instance_name: setupElement('setupName').value,
        administrator_password: setupElement('setupAdminPassword').value,
        client_password: setupElement('setupClientPassword').value
    };
}

async function setupRequest(path, payload) {
    const response = await fetch(path, {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-PortableTAK-Setup': '1'},
        body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'setup_request_failed');
    return result;
}

async function refreshProvisionView() {
    try {
        const response = await fetch('/api/provision/status', {cache: 'no-store', credentials: 'same-origin'});
        if (!response.ok) throw new Error('setup_status_unavailable');
        provisionState = await response.json();
        if (provisionState.ok && provisionState.complete) {
            clearSetupPasswords();
            setupElement('provisionWizard').hidden = true;
            setupElement('portabletakDashboard').hidden = false;
            if (provisioningPoll) clearInterval(provisioningPoll);
            if (!dashboardStarted) {
                dashboardStarted = true;
                refreshStatus();
                refreshClientInfo();
                refreshUsers();
                refreshBackups();
                setInterval(refreshStatus, 3000);
            }
            return;
        }
        setupElement('portabletakDashboard').hidden = true;
        setupElement('provisionWizard').hidden = false;
        setupElement('setupSubmit').disabled = !provisionState.can_provision;
        setupElement('setupValidate').disabled = !provisionState.can_stage;
        if (!provisionState.ok) {
            setupMessage('Cartridge unavailable: ' + (provisionState.error || 'unknown_error'));
        } else if (provisionState.state === 'recovery') {
            setupMessage('This cartridge requires recovery. Create New is unavailable.');
        } else if (provisionState.phase === 'staged') {
            setupMessage('Setup artifacts are staged. Server activation is unavailable in this build.');
        } else if (provisionState.state === 'failed') {
            setupMessage('Setup failed: ' + (provisionState.error || 'unknown_error') + '. Saved staging data is retained.');
        } else {
            setupMessage('New server setup. You can check settings; server activation is unavailable in this build.');
        }
    } catch (error) {
        provisionState = null;
        setupElement('portabletakDashboard').hidden = true;
        setupElement('provisionWizard').hidden = false;
        setupElement('setupSubmit').disabled = true;
        setupElement('setupValidate').disabled = true;
        setupMessage('Unable to check setup status. Refresh to retry.');
    }
}

setupElement('setupRefresh').addEventListener('click', refreshProvisionView);
setupElement('setupValidate').addEventListener('click', async () => {
    const payload = setupPayload();
    if (!payload) return;
    setupElement('setupValidate').disabled = true;
    try {
        await setupRequest('/api/provision/validate', payload);
        setupMessage('Settings are valid. Server activation is unavailable in this build.');
    } catch (error) {
        setupMessage('Check settings: ' + error.message);
    } finally {
        clearSetupPasswords();
        setupElement('setupValidate').disabled = !provisionState?.can_stage;
    }
});
setupElement('provisionForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!provisionState?.can_provision) return;
    const payload = setupPayload();
    if (!payload) return;
    setupElement('setupSubmit').disabled = true;
    try {
        await setupRequest('/api/provision', payload);
        await refreshProvisionView();
        if (!provisioningPoll) provisioningPoll = setInterval(refreshProvisionView, 3000);
    } catch (error) {
        setupMessage('Setup could not start: ' + error.message);
    } finally {
        clearSetupPasswords();
    }
});
refreshProvisionView();
