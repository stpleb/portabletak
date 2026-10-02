const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/provision.js'), 'utf8');

async function harness(state) {
    const elements = new Map();
    const requests = [];
    let dashboardCalls = 0;
    let intervals = 0;
    const element = id => {
        if (!elements.has(id)) elements.set(id, {value: '', hidden: true, disabled: false,
            textContent: '', listeners: {}, reportValidity: () => true,
            addEventListener(name, fn) { this.listeners[name] = fn; }});
        return elements.get(id);
    };
    const context = vm.createContext({
        document: {getElementById: element}, Intl,
        fetch: async (url, options) => {
            requests.push({url, options});
            if (state instanceof Error) throw state;
            return {ok: true, json: async () => url.endsWith('/status') ? state : {ok: true}};
        },
        refreshStatus: () => dashboardCalls++, refreshClientInfo: () => dashboardCalls++,
        refreshUsers: () => dashboardCalls++, refreshBackups: () => dashboardCalls++,
        setInterval: () => ++intervals, clearInterval: () => {},
    });
    vm.runInContext(source, context);
    await new Promise(setImmediate);
    return {element, requests, context, dashboardCalls: () => dashboardCalls, intervals: () => intervals};
}

test('existing cartridge opens dashboard once', async () => {
    const h = await harness({ok: true, complete: true, state: 'existing'});
    assert.equal(h.element('portabletakDashboard').hidden, false);
    assert.equal(h.element('provisionWizard').hidden, true);
    assert.equal(h.dashboardCalls(), 4);
    await vm.runInContext('refreshProvisionView()', h.context);
    assert.equal(h.dashboardCalls(), 4);
    assert.equal(h.intervals(), 1);
});

test('new cartridge cannot run dashboard or submit to unavailable backend', async () => {
    const h = await harness({ok: true, complete: false, state: 'new', can_stage: true, can_provision: false});
    assert.equal(h.element('portabletakDashboard').hidden, true);
    assert.equal(h.element('provisionWizard').hidden, false);
    assert.equal(h.element('setupSubmit').disabled, true);
    assert.equal(h.dashboardCalls(), 0);
    await h.element('provisionForm').listeners.submit({preventDefault() {}});
    assert.equal(h.requests.length, 1);
});

test('unavailable status fails closed', async () => {
    const h = await harness(new Error('offline'));
    assert.equal(h.element('portabletakDashboard').hidden, true);
    assert.equal(h.element('setupSubmit').disabled, true);
    assert.equal(h.element('setupValidate').disabled, true);
    assert.equal(h.dashboardCalls(), 0);
});

test('password mismatch is rejected before network submission', async () => {
    const h = await harness({ok: true, state: 'new', can_stage: true});
    h.element('setupAdminPassword').value = 'one';
    h.element('setupAdminConfirm').value = 'two';
    await h.element('setupValidate').listeners.click();
    assert.equal(h.requests.length, 1);
    assert.match(h.element('provisionMessage').textContent, /must match/);
});

test('validation clears password fields and uses guarded JSON request', async () => {
    const h = await harness({ok: true, state: 'new', can_stage: true});
    for (const id of ['setupAdminPassword','setupAdminConfirm']) h.element(id).value = 'admin-password-1234';
    for (const id of ['setupClientPassword','setupClientConfirm']) h.element(id).value = 'client-password-1234';
    await h.element('setupValidate').listeners.click();
    assert.equal(h.requests[1].url, '/api/provision/validate');
    assert.equal(h.requests[1].options.headers['X-PortableTAK-Setup'], '1');
    for (const id of ['setupAdminPassword','setupAdminConfirm','setupClientPassword','setupClientConfirm']) assert.equal(h.element(id).value, '');
    assert.doesNotMatch(h.element('provisionMessage').textContent, /admin-password|client-password/);
});
