/* Real compiled dabble 0.5 full-stack frontend: classes, imports, DOM and events */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Node {
  constructor() { this.children=[]; this.nodeType=1; }
  appendChild(value) { this.children.push(value); }
  replaceChildren(...items) { this.children=items; }
  get textContent() { return this.children.map(x=>x.textContent).join(''); }
}
class Text extends Node {
  constructor(v) { super(); this.text=String(v);this.nodeType=3; }
  get textContent(){ return this.text; }
  set textContent(v){ this.text=String(v); }
}
class Element extends Node {
  constructor(tag) { super(); this.tag=tag; this.attrs={};this.events={};
    this.classList={add:v=>{this.attrs.class=v}};
    this.style={setProperty:()=>{},removeProperty:()=>{}};
  }
  setAttribute(k,v){this.attrs[k]=v}
  removeAttribute(k){delete this.attrs[k]}
  addEventListener(k,v){this.events[k]=v}
  removeEventListener(k,v){if (this.events[k]===v)delete this.events[k]}
}
const root=new Element('div');
const window={location:{pathname:'/'},addEventListener:()=>{},history:{}};
const document={querySelector:s=>s==='#app'?root:null,
  createElement:tag=>new Element(tag),createTextNode:v=>new Text(v)};
let calls=[];
const context=vm.createContext({Node,window,document,console,
  fetch:async u=>{calls.push(u);return {ok:true,json:async()=>({message:'From backend'})}}});
const example=path.resolve(__dirname,'../examples/fullstack');
vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../runtime.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(example,'app.js'),'utf8'),context);
const app=root.children[0];
assert.equal(app.attrs.class,'dabble-App');
assert.ok(app.textContent.includes('frontend + backend'));
const buttons=app.children.filter(x=>x.tag==='button');
assert.equal(buttons.length,2);
assert.ok(app.textContent.includes('Counter: 0'));
buttons[0].events.click();
assert.ok(app.textContent.includes('Counter: 1'));
Promise.resolve(buttons[1].events.click()).then(()=>{
  assert.deepEqual(calls,['/api/hello']);
  assert.ok(app.textContent.includes('From backend'));
  console.log('dabble 0.5 DOM integration passed');
}).catch(e=>{console.error(e);process.exitCode=1});
