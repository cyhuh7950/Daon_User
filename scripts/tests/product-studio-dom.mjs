export class MinimalEvent {
  constructor(type, options = {}) { this.type = type; this.bubbles = options.bubbles !== false; this.cancelable = options.cancelable !== false; this.defaultPrevented = false; this.cancelBubble = false; this.target = null; this.currentTarget = null; }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this.cancelBubble = true; }
}

class MinimalNode {
  constructor(nodeType, nodeName, ownerDocument = null) { this.nodeType = nodeType; this.nodeName = nodeName; this.ownerDocument = ownerDocument; this.parentNode = null; this.childNodes = []; this.listeners = new Map(); }
  appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  insertBefore(child, before) { child.parentNode = this; const index = this.childNodes.indexOf(before); if (index < 0) this.childNodes.push(child); else this.childNodes.splice(index, 0, child); return child; }
  removeChild(child) { const index = this.childNodes.indexOf(child); if (index >= 0) this.childNodes.splice(index, 1); child.parentNode = null; return child; }
  get firstChild() { return this.childNodes[0] ?? null; }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map((child) => child.textContent).join(""); }
  set textContent(value) { if (this.nodeType === 3) { this.nodeValue = String(value); return; } this.childNodes = []; if (value !== "") this.appendChild(this.ownerDocument.createTextNode(String(value))); }
  addEventListener(type, listener) { const listeners = this.listeners.get(type) ?? []; listeners.push(listener); this.listeners.set(type, listeners); }
  removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter((item) => item !== listener)); }
  dispatchEvent(event) { if (event.type === "click" && this.disabled) return true; if (!event.target) event.target = this; event.currentTarget = this; for (const listener of this.listeners.get(event.type) ?? []) listener.call(this, event); if (event.bubbles && !event.cancelBubble && this.parentNode) this.parentNode.dispatchEvent(event); return !event.defaultPrevented; }
  contains(candidate) { return candidate === this || this.childNodes.some((child) => child.contains?.(candidate)); }
  getRootNode() { let node = this; while (node.parentNode) node = node.parentNode; return node; }
}

class MinimalElement extends MinimalNode {
  constructor(tagName, ownerDocument) { super(1, tagName.toUpperCase(), ownerDocument); this.tagName = this.nodeName; this.namespaceURI = "http://www.w3.org/1999/xhtml"; this.attributes = new Map(); this.style = {}; this.value = ""; this.disabled = false; this.hidden = false; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); if (name === "value") this.value = String(value); if (name === "disabled") this.disabled = true; }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); if (name === "disabled") this.disabled = false; }
  hasAttribute(name) { return this.attributes.has(name); }
  get options() { return this.tagName === "SELECT" ? this.childNodes.filter((child) => child.tagName === "OPTION") : undefined; }
  focus() { this.ownerDocument.activeElement = this; }
}

class MinimalText extends MinimalNode { constructor(value, ownerDocument) { super(3, "#text", ownerDocument); this.nodeValue = String(value); } }

export function findElements(root, predicate, matches = []) { if (root?.nodeType === 1 && predicate(root)) matches.push(root); for (const child of root?.childNodes ?? []) findElements(child, predicate, matches); return matches; }
export function buttonByText(root, label) { return findElements(root, (node) => node.tagName === "BUTTON" && node.textContent.trim() === label)[0]; }

export function installMinimalDom() {
  const document = new MinimalNode(9, "#document", null); document.ownerDocument = document;
  document.createElement = (tag) => new MinimalElement(tag, document); document.createElementNS = (_ns, tag) => new MinimalElement(tag, document); document.createTextNode = (value) => new MinimalText(value, document); document.createComment = (value) => { const node = new MinimalNode(8, "#comment", document); node.nodeValue = value; return node; };
  document.documentElement = document.createElement("html"); document.body = document.createElement("body"); document.documentElement.appendChild(document.body); document.appendChild(document.documentElement); document.activeElement = document.body;
  const window = { document, Event: MinimalEvent, MouseEvent: MinimalEvent, Node: MinimalNode, Element: MinimalElement, HTMLElement: MinimalElement, HTMLIFrameElement: class extends MinimalElement {}, addEventListener: (...args) => document.addEventListener(...args), removeEventListener: (...args) => document.removeEventListener(...args) };
  document.defaultView = window;
  const keys = ["window", "document", "Node", "Element", "HTMLElement", "HTMLIFrameElement", "Event", "MouseEvent", "IS_REACT_ACT_ENVIRONMENT"];
  const prior = Object.fromEntries(keys.map((key) => [key, globalThis[key]])); Object.assign(globalThis, { window, document, Node: MinimalNode, Element: MinimalElement, HTMLElement: MinimalElement, HTMLIFrameElement: window.HTMLIFrameElement, Event: MinimalEvent, MouseEvent: MinimalEvent, IS_REACT_ACT_ENVIRONMENT: true });
  return { document, restore: () => { for (const [key, value] of Object.entries(prior)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } } };
}
