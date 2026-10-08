const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../assets/pulsar-light.js'), 'utf8');

function setup(options = {}) {
  class Events {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, callback) {
      if (!this.listeners.has(name)) this.listeners.set(name, new Set());
      this.listeners.get(name).add(callback);
    }
    removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
    emit(name, event = {}) { for (const callback of this.listeners.get(name) || []) callback(event); }
  }
  class Element extends Events {
    constructor() {
      super(); this.attrs = {}; this.children = []; this.isConnected = true;
      const values = new Set();
      this.classList = {
        add: (...names) => names.forEach(name => values.add(name)),
        remove: (...names) => names.forEach(name => values.delete(name)),
        contains: name => values.has(name),
        toggle: (name, force) => force ? values.add(name) : values.delete(name)
      };
    }
    setAttribute(name, value) { this.attrs[name] = value; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    appendChild(child) { child.parent = this; this.children.push(child); }
    remove() { this.isConnected = false; this.parent.children = this.parent.children.filter(child => child !== this); }
    getBoundingClientRect() { return { width: options.width || 480, height: options.height || 430, left: 0, top: 0 }; }
  }
  const host = new Element(); host.setAttribute('data-pulsar-webgl', 'light');
  const root = new Element(); root.setAttribute('data-color-theme', options.theme || 'calm');
  const motion = new Events(); motion.matches = !!options.reduced;
  const fine = new Events(); fine.matches = !options.mobile;
  const canvas = new Element();
  const gl = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, TRIANGLES: 5, NO_ERROR: 0,
    draws: [], contexts: 0, deletedPrograms: 0, lost: false,
    createShader: () => ({}), shaderSource() {}, compileShader() {}, deleteShader() {},
    getShaderParameter: () => options.compile !== false,
    createProgram: () => ({}), attachShader() {}, linkProgram() {},
    getProgramParameter: () => options.link !== false,
    getUniformLocation: (_, name) => name,
    useProgram() {}, viewport() {},
    uniform2f(name, x, y) { this[name] = [x, y]; },
    uniform1f(name, value) { this[name] = value; },
    drawArrays() { this.draws.push({ time: this.uTime, theme: this.uCalm, pointer: this.uPointer.slice() }); },
    isContextLost() { return this.lost; },
    getError: () => options.drawError ? 1 : 0,
    deleteProgram() { this.deletedPrograms++; }
  };
  canvas.getContext = () => { gl.contexts++; return options.webgl === false ? null : gl; };
  const document = new Events(); document.hidden = false; document.documentElement = root;
  document.querySelector = () => options.noHost ? null : host;
  document.createElement = name => { assert.equal(name, 'canvas'); return canvas; };
  const window = new Events();
  window.location = { search: options.search || '' };
  window.innerWidth = options.mobile ? 390 : 1440;
  window.devicePixelRatio = options.dpr || 3;
  window.matchMedia = query => query.includes('reduced-motion') ? motion : fine;
  const observers = [];
  class Observer {
    constructor(callback) { this.callback = callback; this.connected = true; observers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.connected = false; }
    emit(value) { if (this.connected) this.callback(value); }
  }
  class SizeObserver extends Observer { constructor(callback) { super(callback); this.kind = 'size'; } }
  class IntersectionObserver extends Observer { constructor(callback) { super(callback); this.kind = 'intersection'; } }
  class MutationObserver extends Observer { constructor(callback) { super(callback); this.kind = 'mutation'; } }
  window.IntersectionObserver = IntersectionObserver;
  window.ResizeObserver = SizeObserver;
  window.MutationObserver = MutationObserver;
  const pending = new Map(); let nextId = 0, stamp = 0;
  const context = { window, document, URLSearchParams, Math,
    IntersectionObserver, ResizeObserver: SizeObserver, MutationObserver,
    requestAnimationFrame(callback) { pending.set(++nextId, callback); return nextId; },
    cancelAnimationFrame(id) { pending.delete(id); }
  };
  vm.runInNewContext(source, context);
  return { host, root, motion, canvas, gl, window, document, pending, observers,
    frames(count = 1, increment = 1000 / 60) {
      for (let i = 0; i < count; i++) {
        stamp += increment;
        const callbacks = [...pending.values()]; pending.clear();
        for (const callback of callbacks) callback(stamp);
      }
    },
    show(value = true) { observers.find(item => item.kind === 'intersection')?.emit([{ isIntersecting: value }]); },
    theme(value) { root.setAttribute('data-color-theme', value); observers.find(item => item.target === root)?.emit([]); },
    mode(value) { host.setAttribute('data-pulsar-webgl', value); observers.filter(item => item.target === host).at(-1)?.emit([]); }
  };
}

test('original mode and unrelated pages keep their existing DOM', () => {
  for (const options of [{ search: '?pulsar=original' }, { noHost: true }]) {
    const app = setup(options);
    assert.equal(app.host.children.length, 0);
    assert.equal(app.gl.contexts, 0);
    assert.equal(app.observers.length, 0);
  }
});

test('WebGL initializes only for visible artwork, within desktop and mobile budgets', () => {
  for (const mobile of [false, true]) {
    const app = setup({ mobile, width: mobile ? 342 : 900, height: mobile ? 280 : 430 });
    app.frames(10); assert.equal(app.gl.contexts, 0);
    app.show(); app.frames(120);
    assert.equal(app.gl.contexts, 1);
    assert.ok(app.host.classList.contains('is-pulsar-ready'));
    assert.ok(app.canvas.width * app.canvas.height <= (mobile ? 180000 : 480000));
    assert.ok(app.gl.draws.length > 30 && app.gl.draws.length <= 61);
    assert.ok(app.gl.draws.at(-1).time > 10);
  }
});

test('hidden artwork, hidden tabs and the back-forward cache stop drawing', () => {
  const app = setup(); app.show(); app.frames(12);
  for (const [hide, show] of [
    [() => app.show(false), () => app.show(true)],
    [() => { app.document.hidden = true; app.document.emit('visibilitychange'); }, () => { app.document.hidden = false; app.document.emit('visibilitychange'); }],
    [() => app.window.emit('pagehide', { persisted: true }), () => app.window.emit('pageshow')]
  ]) {
    const count = app.gl.draws.length, time = app.gl.draws.at(-1).time;
    hide(); assert.equal(app.pending.size, 0); app.frames(60, 100);
    assert.equal(app.gl.draws.length, count);
    show(); app.frames(1);
    assert.equal(app.gl.draws.at(-1).time, time);
    app.frames(10); assert.ok(app.gl.draws.length > count);
  }
});

test('reduced motion draws a static frame and still responds to theme changes', () => {
  const app = setup({ reduced: true }); app.show(); app.frames(60);
  assert.equal(app.gl.draws.length, 1);
  assert.equal(app.pending.size, 0);
  assert.equal(app.gl.draws[0].theme, 1);
  app.theme('neon'); app.frames(10);
  assert.equal(app.gl.draws.length, 2);
  assert.equal(app.gl.draws[1].theme, 0);
  assert.equal(app.pending.size, 0);
  app.motion.matches = false; app.motion.emit('change'); app.frames(60);
  assert.ok(app.gl.draws.at(-1).time > 9);
  app.motion.matches = true; app.motion.emit('change'); app.frames(60);
  assert.equal(app.pending.size, 0);
});

test('theme and pointer updates remain confined to artwork', () => {
  const app = setup(); app.show(); app.frames(5);
  app.theme('neon'); app.host.emit('pointermove', { clientX: 470, clientY: 10 }); app.frames(120);
  assert.equal(app.gl.draws.at(-1).theme, 0);
  assert.ok(app.gl.draws.at(-1).pointer[0] > .8);
  assert.ok(app.gl.draws.at(-1).pointer[1] > .8);
  app.theme('calm'); app.frames(4); assert.equal(app.gl.draws.at(-1).theme, 1);
  assert.deepEqual(Object.keys(app.root.attrs), ['data-color-theme']);
});

test('unavailable WebGL and shader failures keep the original artwork', () => {
  for (const options of [{ webgl: false }, { compile: false }, { link: false }, { drawError: true }]) {
    const app = setup(options); app.show(); app.frames(3);
    assert.ok(!app.host.classList.contains('is-pulsar-ready'));
    assert.equal(app.host.children.length, 0);
    assert.equal(app.pending.size, 0);
    assert.ok(app.observers.every(item => !item.connected));
  }
});

test('context loss reveals the original and restoration recreates WebGL', () => {
  const app = setup(); app.show(); app.frames(5);
  app.gl.lost = true;
  let prevented = false;
  app.canvas.emit('webglcontextlost', { preventDefault() { prevented = true; } });
  assert.ok(prevented);
  assert.ok(!app.host.classList.contains('is-pulsar-ready'));
  assert.equal(app.pending.size, 0);
  app.gl.lost = false; app.canvas.emit('webglcontextrestored'); app.frames(5);
  assert.ok(app.host.classList.contains('is-pulsar-ready'));
  assert.equal(app.gl.contexts, 2);
});

test('runtime rollback and page removal release resources and expose the original', () => {
  for (const remove of [app => app.mode('original'), app => app.window.emit('pagehide', { persisted: false }), app => { app.host.isConnected = false; }]) {
    const app = setup(); app.show(); app.frames(5); remove(app); app.frames(2);
    assert.ok(!app.host.classList.contains('is-pulsar-ready'));
    assert.equal(app.host.children.length, 0);
    assert.equal(app.pending.size, 0);
    assert.equal(app.gl.deletedPrograms, 1);
    assert.ok(app.observers.every(item => !item.connected));
  }
});
