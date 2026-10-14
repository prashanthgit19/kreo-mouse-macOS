// Kreo Hawk Pro — protocol oracle.
//
// Runs Kreo's own WebAssembly driver (yuandaxin) with a fake HID transport and
// records every report it tries to send. Reveals the wire protocol with no USB
// sniffing and no Windows.
//
//   node oracle.mjs schema                     dump getDeviceInfoSchema() -> captures/schema-hawk-pro.json
//   node oracle.mjs call <method> '<json>'     call a driver method, print reports emitted
//   node oracle.mjs macro-read                 readMacroSlots()
//
// Flags:
//   --live           route feature reads/writes to the real mouse via ../hidtool
//   --allow-write    permit feature writes when --live (writes to hardware!)
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import path from 'node:path';
import init, { YuandaxinDriver } from './vendor/yuandaxin-driver-glue.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const capsDir = path.join(__dirname, '..', 'captures');
mkdirSync(capsDir, { recursive: true });

const wasmPath = path.join(__dirname, 'vendor', 'kontrol-mod-c8d14f_bg-BmYXPgde.wasm');
const descPath = path.join(__dirname, '..', 'device-descriptor.json');
const hidtool = path.join(__dirname, '..', 'hidtool');

const argv = process.argv.slice(2);
const FLAGS = new Set(argv.filter((a) => a.startsWith('--')));
const POS = argv.filter((a) => !a.startsWith('--'));
const LIVE = FLAGS.has('--live');
const ALLOW_WRITE = FLAGS.has('--allow-write');

const descriptor = JSON.parse(readFileSync(descPath, 'utf8'));
await init({ module_or_path: readFileSync(wasmPath) });

// ---- transport ------------------------------------------------------------
const KNOWN_RESPONSES = {
  5: '05 31 30 31 30 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
  6: '06 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
  188: 'BC 01 00 00 00 00 00',
  189: 'BD ' + '01 00 '.repeat(67).trim(),
  191: 'BF ' + '01 00 '.repeat(67).trim()
};

function hex(bytes) {
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
}

function startHidServer() {
  const srv = spawn(hidtool, ['server'], { stdio: ['pipe', 'pipe', 'inherit'] });
  srv.stdout.setEncoding('utf8');
  const waiters = [];
  let buf = '';
  srv.stdout.on('data', (chunk) => {
    buf += chunk;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      (waiters.shift() ?? (() => {}))(line);
    }
  });
  return {
    ready: new Promise((r) => waiters.push(r)),
    send: (line) => new Promise((r) => { waiters.push(r); srv.stdin.write(line + '\n'); }),
    stop: () => { try { srv.stdin.write('QUIT\n'); } catch {} }
  };
}

const trace = [];
let srv = null;
if (LIVE) { srv = startHidServer(); await srv.ready; }

const transport = {
  async sendFeatureReport(id, data) {
    const bytes = data instanceof Uint8Array ? data : Uint8Array.from(data ?? []);
    let outcome = 'mock';
    if (LIVE) {
      if (!ALLOW_WRITE) outcome = 'BLOCKED(no --allow-write)';
      else { const r = await srv.send(`SET ${id} ${hex(bytes)}`); outcome = r; }
    }
    trace.push({ op: 'SET', id, len: bytes.length, data: hex(bytes), outcome });
    return bytes.length;
  },
  async receiveFeatureReport(id) {
    if (LIVE) {
      const resp = await srv.send(`GET ${id} 135`);
      if (resp.startsWith('OK')) {
        const bytes = Uint8Array.from(resp.slice(3).trim().split(/\s+/).filter(Boolean).map((h) => parseInt(h, 16)));
        trace.push({ op: 'GET', id, len: bytes.length, data: hex(bytes).slice(0, 120) });
        return bytes;
      }
      trace.push({ op: 'GET', id, error: resp });
      return new Uint8Array();
    }
    const k = KNOWN_RESPONSES[id];
    if (k) trace.push({ op: 'GET', id, mock: true, data: k.slice(0, 120) });
    else trace.push({ op: 'GET', id, mock: true, empty: true });
    return k ? Uint8Array.from(k.trim().split(/\s+/).map((h) => parseInt(h, 16))) : new Uint8Array();
  },
  async sendOutputReport(id, data) {
    const bytes = data instanceof Uint8Array ? data : Uint8Array.from(data ?? []);
    trace.push({ op: 'OUT', id, len: bytes.length, data: hex(bytes) });
    return bytes.length;
  },
  async sleep(ms) { return new Promise((r) => setTimeout(r, Math.min(ms ?? 0, 20))); },
  notifyStateChanged() { trace.push({ op: 'notify' }); }
};

// ---- driver ---------------------------------------------------------------
const driver = new YuandaxinDriver('Kreo Hawk Pro', false);
driver.setTransportCallbacks(transport);
driver.setHidDescriptorContext(descriptor);
await driver.connect();

function fmtTrace(mark) {
  return trace.slice(mark).map((t) => '   ' + JSON.stringify(t)).join('\n');
}

const cmd = POS[0] ?? 'schema';

try {
  if (cmd === 'schema') {
    const s = driver.getDeviceInfoSchema();
    writeFileSync(path.join(capsDir, 'schema-hawk-pro.json'), JSON.stringify(s, null, 2));
    console.log('schema -> captures/schema-hawk-pro.json');
    console.log('capabilities keys:', Object.keys(s.capabilities ?? {}).join(', '));
    console.log('state keys:', Object.keys(s.state ?? {}).join(', '));
    console.log(JSON.stringify(s, null, 2).slice(0, 2500));
  } else if (cmd === 'call') {
    const method = POS[1];
    const payload = POS[2] ? JSON.parse(POS[2]) : {};
    const mark = trace.length;
    console.log(`call ${method} ${JSON.stringify(payload)}`);
    let result;
    try { result = await driver[method](payload); }
    catch (e) { console.log('  threw:', e?.message ?? e); }
    console.log('reports emitted:\n' + (fmtTrace(mark) || '   (none)'));
    if (result !== undefined) console.log('result:', JSON.stringify(result).slice(0, 800));
  } else if (cmd === 'sweep') {
    const scenarios = JSON.parse(readFileSync(POS[1], 'utf8'));
    for (const sc of scenarios) {
      const mark = trace.length;
      console.log(`\n## ${sc.label ?? sc.method}  ${JSON.stringify(sc.payload)}`);
      try {
        const r = await driver[sc.method](sc.payload);
        if (r !== undefined) console.log('   result:', JSON.stringify(r).slice(0, 300));
      } catch (e) { console.log('   threw:', e?.message ?? e); }
      console.log(fmtTrace(mark) || '   (no reports)');
    }
  } else if (cmd === 'feed') {
    const rid = parseInt(POS[1], 0);
    const bytes = Uint8Array.from(POS[2].trim().split(/\s+/).map((h) => parseInt(h, 16)));
    const before = JSON.stringify(driver.getDeviceInfoSchema().state);
    try { driver.handleInputReport(rid, bytes); } catch (e) { console.log('handleInputReport threw:', e?.message ?? e); }
    const after = JSON.stringify(driver.getDeviceInfoSchema().state);
    console.log('state before:', before.slice(0, 200));
    console.log('state after :', after.slice(0, 200));
    console.log('changed:', before !== after);
  } else if (cmd === 'macro-read') {
    const mark = trace.length;
    try { console.log('readMacroSlots ->', JSON.stringify(await driver.readMacroSlots()).slice(0, 800)); }
    catch (e) { console.log('threw:', e?.message ?? e); }
    console.log('reports:\n' + fmtTrace(mark));
  } else {
    console.log('unknown command', cmd);
  }
} finally {
  if (srv) srv.stop();
}
