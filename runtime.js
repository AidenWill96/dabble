/* dabble 0.2 browser runtime. No third-party dependencies. */
(() => {
  "use strict";

  let activeReaction = null;
  let activeOwner = null;
  const mountedOwners = new WeakMap();

  function own(disposer) {
    if (activeOwner) activeOwner.add(disposer);
    return disposer;
  }

  function signal(initial) {
    let value = initial;
    const observers = new Set();
    const observable = {
      __dabbleSignal: true,
      get() {
        if (activeReaction) activeReaction.track(observable);
        return value;
      },
      set(next) {
        if (Object.is(next, value)) return;
        value = next;
        for (const notify of Array.from(observers)) notify(value);
      },
      subscribe(callback) {
        if (typeof callback !== "function") throw new TypeError("subscribe requires a function");
        observers.add(callback);
        return () => observers.delete(callback);
      }
    };
    return observable;
  }

  function isSignal(value) {
    return value !== null && typeof value === "object" && value.__dabbleSignal === true;
  }

  function effect(fn) {
    if (typeof fn !== "function") throw new TypeError("effect requires a function");
    let subscriptions = new Map();
    let disposed = false;
    let running = false;
    let pending = false;
    const observer = {
      track(source) {
        if (!subscriptions.has(source)) subscriptions.set(source, source.subscribe(run));
      }
    };
    function clear() {
      for (const unsubscribe of subscriptions.values()) unsubscribe();
      subscriptions.clear();
    }
    function run() {
      if (disposed) return;
      if (running) { pending = true; return; }
      running = true;
      try {
        do {
          pending = false;
          clear();
          const previous = activeReaction;
          activeReaction = observer;
          try { fn(); } finally { activeReaction = previous; }
        } while (pending && !disposed);
      } finally { running = false; }
    }
    const dispose = () => { disposed = true; clear(); };
    own(dispose);
    try { run(); } catch (error) { dispose(); throw error; }
    return dispose;
  }

  function computed(fn) {
    if (typeof fn !== "function") throw new TypeError("computed requires a function");
    const value = signal(undefined);
    const stop = effect(() => value.set(fn()));
    return Object.freeze({
      __dabbleSignal: true,
      get: value.get,
      subscribe: value.subscribe,
      dispose: stop
    });
  }

  function display(value) { return value == null ? "" : String(value); }

  function bindSignal(value, render) {
    render(value.get());
    own(value.subscribe(render));
  }

  function appendChild(parent, child) {
    if (child == null || child === false || child === true) return;
    if (Array.isArray(child)) {
      for (const item of child) appendChild(parent, item);
    } else if (isSignal(child)) {
      const text = document.createTextNode(display(child.get()));
      own(child.subscribe(value => { text.textContent = display(value); }));
      parent.appendChild(text);
    } else if (child instanceof Node) {
      parent.appendChild(child);
    } else if (typeof child === "string" || typeof child === "number") {
      parent.appendChild(document.createTextNode(String(child)));
    } else {
      throw new TypeError("Cannot render dabble child: " + typeof child);
    }
  }

  function applyProps(element, props) {
    for (const [rawKey, value] of Object.entries(props)) {
      const key = rawKey === "class_" ? "class"
        : rawKey === "html_for" ? "for"
        : rawKey.startsWith("data_") ? rawKey.replaceAll("_", "-")
        : rawKey.startsWith("aria_") ? rawKey.replaceAll("_", "-")
        : rawKey;
      if (key.startsWith("on")) {
        if (typeof value !== "function") throw new TypeError("event prop " + key + " requires a callback");
        const type = key.slice(2).toLowerCase();
        element.addEventListener(type, value);
        own(() => element.removeEventListener(type, value));
      } else if (key === "style" && value && typeof value === "object" && !isSignal(value)) {
        for (const [name, entry] of Object.entries(value)) {
          const prop = name.replaceAll("_", "-");
          const render = v => {
            if (v == null || v === false) element.style.removeProperty(prop);
            else element.style.setProperty(prop, String(v));
          };
          if (isSignal(entry)) bindSignal(entry, render);
          else render(entry);
        }
      } else {
        const render = v => {
          if (key === "value" || key === "checked" || key === "disabled" || key === "hidden") {
            element[key] = v == null ? (key === "value" ? "" : false) : v;
          } else if (v === false || v == null) {
            element.removeAttribute(key);
          } else if (v === true) {
            element.setAttribute(key, "");
          } else {
            element.setAttribute(key, String(v));
          }
        };
        if (isSignal(value)) bindSignal(value, render);
        else render(value);
      }
    }
  }

  function el(tag, ...args) {
    if (typeof tag !== "string" || !/^[a-z][a-z0-9-]*$/.test(tag)) {
      throw new TypeError("HTML tag must be a lowercase tag name");
    }
    const element = document.createElement(tag);
    const tail = args.at(-1);
    if (tail && !isSignal(tail) && Object.getPrototypeOf(tail) !== null && Object.getPrototypeOf(Object.getPrototypeOf(tail)) === null) {
      args.pop();
      applyProps(element, tail);
    }
    for (const child of args) appendChild(element, child);
    return element;
  }

  function scope(node, className) {
    if (!(node instanceof Node) || node.nodeType !== 1) {
      throw new TypeError("styled components must return one root HTML element");
    }
    node.classList.add(className);
    return node;
  }

  function mount(selector, component) {
    const root = document.querySelector(selector);
    if (!root) throw new Error("dabble mount target not found: " + selector);
    const previous = mountedOwners.get(root);
    if (previous) {
      for (const dispose of Array.from(previous).reverse()) dispose();
    }
    const owner = new Set();
    mountedOwners.set(root, owner);
    root.replaceChildren();
    const previousOwner = activeOwner;
    activeOwner = owner;
    try {
      const tree = typeof component === "function" ? component() : component;
      appendChild(root, tree);
    } catch (error) {
      for (const dispose of Array.from(owner).reverse()) dispose();
      root.replaceChildren();
      throw error;
    } finally {
      activeOwner = previousOwner;
    }
    return root;
  }

  function truthy(x) {
    if (x == null || x === false || x === 0 || x === "" || Number.isNaN(x)) return false;
    if (Array.isArray(x)) return x.length > 0;
    if (typeof x === "object" && !isSignal(x)) return Object.keys(x).length > 0;
    return true;
  }

  function* range(...args) {
    if (args.length < 1 || args.length > 3) throw new TypeError("range expects 1 to 3 arguments");
    const [start, stop, step] = args.length === 1 ? [0, args[0], 1]
      : args.length === 2 ? [args[0], args[1], 1] : args;
    if (![start, stop, step].every(Number.isInteger) || step === 0) {
      throw new TypeError("range expects integers and a nonzero step");
    }
    for (let i = start; step > 0 ? i < stop : i > stop; i += step) yield i;
  }

  function len(x) {
    if (x == null || typeof x.length !== "number") throw new TypeError("len expects a string or array");
    return x.length;
  }

  async function fetch_json(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status} fetching ${url}`);
    return response.json();
  }

  async function post_json(url, data) {
    const response = await fetch(url, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} posting ${url}`);
    return response.json();
  }

  function storage_get(key, fallback = null) {
    const item = localStorage.getItem(key);
    return item === null ? fallback : JSON.parse(item);
  }

  function storage_set(key, value) { localStorage.setItem(key, JSON.stringify(value)); }

  const locationSignal = signal(window.location ? window.location.pathname : "/");
  if (window.addEventListener) {
    window.addEventListener("popstate", () => locationSignal.set(window.location.pathname));
  }
  function path() { return locationSignal; }
  function navigate(target) {
    if (typeof target !== "string" || !target.startsWith("/") || target.startsWith("//")) {
      throw new TypeError("navigate requires a same-origin absolute path");
    }
    // file:// demos have no origin-relative History API navigation.
    if (window.location.protocol === "file:") { locationSignal.set(target); return; }
    window.history.pushState(null, "", target);
    locationSignal.set(window.location.pathname);
  }
  function on_route(callback) {
    if (typeof callback !== "function") throw new TypeError("on_route requires a function");
    const stop = locationSignal.subscribe(callback);
    own(stop);
    callback(locationSignal.get());
    return stop;
  }

  const api = {
    el, mount, scope, signal, computed, effect, range, len, str: String,
    print: console.log, truthy, fetch_json, post_json, storage_get, storage_set,
    navigate, path, on_route
  };
  const tags = "a article aside b br button code div em footer form h1 h2 h3 header img label li main nav ol option p pre section select small span strong textarea ul".split(" ");
  for (const tag of tags) api[tag] = (...args) => el(tag, ...args);
  api.input_ = (...args) => el("input", ...args);
  window.dabble = Object.freeze(api);
})();
