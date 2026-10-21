/* Minimal DOM-integration test: run with node tests/test_runtime.cjs */
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
  constructor(value) { super(); this.text = value; }
  get textContent() { return this.text; }
  set textContent(value) { this.text = String(value); }
}
class FakeElement extends FakeNode {
  constructor(tag) {
    super();
    this.tag = tag;
    this.attributes = {};
    this.listeners = {};
    this.style = {setProperty: (key, value) => { this.attributes[`style:${key}`] = value; }};
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}

const root = new FakeElement('div');
const document = {
  createTextNode: value => new FakeText(value),
  createElement: tag => new FakeElement(tag),
  querySelector: selector => selector === '#app' ? root : null
};
const context = vm.createContext({ Node: FakeNode, document, window: {}, console });
const project = path.resolve(__dirname, '..');
for (const file of ['runtime.js', 'examples/counter.js']) {
  vm.runInContext(fs.readFileSync(path.join(project, file), 'utf8'), context, { filename: file });
}

function find(node, tag) {
  if (node.tag === tag) return node;
  for (const child of node.children) {
    const found = find(child, tag);
    if (found) return found;
  }
  return null;
}

const app = root.children[0];
assert.equal(app.tag, 'main');
assert.equal(find(app, 'h1').textContent, 'Hello from dabble');
assert.equal(find(app, 'button').attributes.class, 'primary');
assert.equal(find(app, 'input').value, 'world');
assert.equal(app.children[4].textContent, 'Hello, world!');
assert.equal(app.children[5].textContent, 'Clicks: 0');

find(app, 'button').listeners.click();
find(app, 'button').listeners.click();
assert.equal(app.children[5].textContent, 'Clicks: 2');
find(app, 'input').listeners.input({target: {value: 'Ada'}});
assert.equal(app.children[4].textContent, 'Hello, Ada!');
console.log('DOM integration passed: mount, events, signal updates');
