/* Integration test of dabble 0.2 state, DOM updates, routing, networking and lifetime. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FakeNode {
  constructor() { this.children = []; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...items) { this.children = items; }
  get textContent() { return this.children.map(c => c.textContent).join(''); }
}
class FakeText extends FakeNode {
  constructor(value) { super(); this.text = String(value); }
  get textContent() { return this.text; }
  set textContent(value) { this.text = String(value); }
}
class FakeElement extends FakeNode {
  constructor(tag) {
    super(); this.tag = tag; this.attrs = {}; this.events = {};
    this.style = {
      setProperty: (name, value) => { this.attrs['style:' + name] = value; },
      removeProperty: name => { delete this.attrs['style:' + name]; }
    };
  }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener(type, cb) { this.events[type] = cb; }
  removeEventListener(type, cb) { if (this.events[type] === cb) delete this.events[type]; }
}
const root = new FakeElement('div');
const storage = new Map();
const window = {
  location: {pathname: '/'}, listeners: {},
  addEventListener(type, cb) { this.listeners[type] = cb; },
  history: {pushState(_unused, _title, url) { window.location.pathname = url; }}
};
const document = {
  createTextNode: value => new FakeText(value),
  createElement: tag => new FakeElement(tag),
  querySelector: selector => selector === '#app' ? root : null
};
let nextFetchFailure = false;
let fetchCalls = [];
const context = vm.createContext({
  Node: FakeNode, document, window, console,
  localStorage: {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, value)
  },
  fetch: async (url, options) => {
    fetchCalls.push({url, options});
    return {ok: !nextFetchFailure, status: nextFetchFailure ? 500 : 200,
      json: async () => ({title: 'A remote task', id: 12})};
  }
});
const project = path.resolve(__dirname, '..');
vm.runInContext(fs.readFileSync(path.join(project, 'runtime.js'), 'utf8'), context);
const d = window.dabble;
function find(node, tag) {
  if (node.tag === tag) return node;
  for (const c of node.children) { const hit = find(c, tag); if (hit) return hit; }
  return null;
}
function findAll(node, tag, found = []) {
  if (node.tag === tag) found.push(node);
  for (const c of node.children) findAll(c, tag, found);
  return found;
}

async function main() {
  // Pure computed state and effect dependency tracking.
  const first = d.signal(2), second = d.signal(7), chooseFirst = d.signal(true);
  const derived = d.computed(() => chooseFirst.get() ? first.get() * 2 : second.get() + 1);
  let calls = [];
  const stop = d.effect(() => calls.push(derived.get()));
  assert.equal(derived.get(), 4);
  first.set(3);
  assert.deepEqual(calls, [4, 6]);
  chooseFirst.set(false);
  assert.equal(derived.get(), 8);
  first.set(999); // old conditional dependency must be detached
  assert.equal(derived.get(), 8);
  second.set(10);
  assert.equal(derived.get(), 11);
  stop();
  derived.dispose();
  second.set(100);
  assert.equal(calls.at(-1), 11);

  // Components with derived children and attributes; remount disposes subscriptions.
  const number = d.signal(0);
  const flavor = d.computed(() => number.get() > 2 ? 'big' : 'small');
  let watched = 0;
  function Card() {
    d.effect(() => { number.get(); watched++; });
    return d.div(
      d.p(number, {class_: flavor, style: {color: flavor}}),
      d.input_({value: number}),
      d.button('Add', {onclick: () => number.set(number.get() + 1)})
    );
  }
  d.mount('#app', Card);
  const oldCard = root.children[0];
  const output = find(oldCard, 'p');
  const oldButton = find(oldCard, 'button');
  assert.equal(output.textContent, '0');
  assert.equal(output.attrs.class, 'small');
  oldButton.events.click(); oldButton.events.click(); oldButton.events.click();
  assert.equal(output.textContent, '3');
  assert.equal(output.attrs.class, 'big');
  assert.equal(output.attrs['style:color'], 'big');
  assert.equal(find(oldCard, 'input').value, 3);
  assert.equal(watched, 4);
  d.mount('#app', Card);
  assert.equal(oldButton.events.click, undefined, 'old handler removed');
  const previousOutput = output.textContent;
  number.set(4);
  assert.equal(output.textContent, previousOutput, 'old DOM unsubscribed');
  assert.equal(find(root, 'p').textContent, '4');
  assert.equal(watched, 6);

  // Native APIs: storage, navigation, fetch JSON and POST JSON.
  assert.equal(d.storage_get('missing', 'fallback'), 'fallback');
  d.storage_set('preferences', {theme: 'dark'});
  assert.equal(d.storage_get('preferences').theme, 'dark');
  assert.equal(d.path().get(), '/');
  d.navigate('/about');
  assert.equal(d.path().get(), '/about');
  window.location.pathname = '/back';
  window.listeners.popstate();
  assert.equal(d.path().get(), '/back');
  assert.equal((await d.fetch_json('/thing')).title, 'A remote task');
  await d.post_json('/thing', {value: 1});
  assert.equal(fetchCalls.at(-1).options.method, 'POST');
  assert.equal(fetchCalls.at(-1).options.body, '{"value":1}');
  nextFetchFailure = true;
  await assert.rejects(d.fetch_json('/bad'), /HTTP 500/);
  nextFetchFailure = false;

  // Real compiled dashboard runs end-to-end in a fake DOM.
  vm.runInContext(fs.readFileSync(path.join(project, 'examples/dashboard.js'), 'utf8'), context);
  const app = root.children[0];
  assert.equal(find(app, 'h1').textContent, 'Write less. Build for the web.');
  const buttons = findAll(app, 'button');
  const counterButton = buttons.find(b => b.textContent === 'Clicks: 0');
  assert.ok(counterButton);
  counterButton.events.click();
  assert.equal(counterButton.textContent, 'Clicks: 1');
  assert.ok(app.textContent.includes('Double: 2'));
  buttons.find(b => b.textContent === 'About').events.click();
  assert.ok(app.textContent.includes('Route: /about'));
  await buttons.find(b => b.textContent === 'Fetch example JSON').events.click();
  assert.ok(app.textContent.includes('API response: A remote task'));
  // Native dabble 0.3 constructs (`component` and `state`) end-to-end.
  vm.runInContext(fs.readFileSync(path.join(project, 'examples/native.js'), 'utf8'), context);
  const nativeApp = root.children[0];
  assert.equal(find(nativeApp, 'h1').textContent, 'dabble 0.3 — our own compiler');
  assert.ok(nativeApp.textContent.includes('Count: 0'));
  assert.ok(nativeApp.textContent.includes('Double: 0'));
  find(nativeApp, 'button').events.click();
  assert.ok(nativeApp.textContent.includes('Count: 1'));
  assert.ok(nativeApp.textContent.includes('Double: 2'));
  console.log('dabble v0.2/v0.3 DOM integration passed: state, computed values, APIs and native syntax');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
