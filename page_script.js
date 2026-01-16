window.MultimodalWebSurfer = (function() {
  // === State ===
  let nextLabel = 10;
  // === Role mapping for approximate ARIA role inference ===
  const roleMapping = {
    "a": "link",
    "area": "link",
    "button": "button",
    "input, type=button": "button",
    "input, type=checkbox": "checkbox",
    "input, type=email": "textbox",
    "input, type=number": "spinbutton",
    "input, type=radio": "radio",
    "input, type=range": "slider",
    "input, type=reset": "button",
    "input, type=search": "searchbox",
    "input, type=submit": "button",
    "input, type=tel": "textbox",
    "input, type=text": "textbox",
    "input, type=url": "textbox",
    "search": "search",
    "select": "combobox",
    "option": "option",
    "textarea": "textbox"
  };
  // === Helpers ===
  const getCursor = (elm) => window.getComputedStyle(elm)["cursor"];
  const getInteractiveElements = function() {
    const results = [];
    const roles = [
      "scrollbar","searchbox","slider","spinbutton","switch","tab","treeitem",
      "button","checkbox","gridcell","link","menuitem","menuitemcheckbox","menuitemradio",
      "option","progressbar","radio","textbox","combobox","menu","tree","treegrid",
      "grid","listbox","radiogroup","widget"
    ];
    const inertCursors = ["auto","default","none","text","vertical-text","not-allowed","no-drop"];
    // 1) Basic interactive selectors
    let nodeList = document.querySelectorAll(
      "input, select, textarea, button, [href], [onclick], [contenteditable], [tabindex]:not([tabindex='-1'])"
    );
    for (let i = 0; i < nodeList.length; i++) results.push(nodeList[i]);
    // 2) Any element with supported role
    nodeList = document.querySelectorAll("[role]");
    for (let i = 0; i < nodeList.length; i++) {
      if (results.indexOf(nodeList[i]) === -1) {
        const role = nodeList[i].getAttribute("role");
        if (roles.indexOf(role) > -1) results.push(nodeList[i]);
      }
    }
    // 3) Any element that changes the cursor to something interactive
    nodeList = document.querySelectorAll("*");
    for (let i = 0; i < nodeList.length; i++) {
      let node = nodeList[i];
      const cursor = getCursor(node);
      if (inertCursors.indexOf(cursor) >= 0) continue;
      let parent = node.parentNode;
      while (parent && getCursor(parent) === cursor) {
        node = parent;
        parent = node.parentNode;
      }
      if (results.indexOf(node) === -1) results.push(node);
    }
    return results;
  };
  const labelElements = function(elements) {
    for (let i = 0; i < elements.length; i++) {
      if (!elements[i].hasAttribute("__elementId")) {
        elements[i].setAttribute("__elementId", "" + (nextLabel++));
      }
    }
  };
  const isTopmost = function(element, x, y) {
    let hit = document.elementFromPoint(x, y);
    if (hit === null) return true;
    while (hit) {
      if (hit === element) return true;
      hit = hit.parentNode;
    }
    return false;
  };
  const trimmedInnerText = function(element) {
    if (!element) return "";
    const text = element.innerText;
    if (!text) return "";
    return text.trim();
  };
  const getApproximateAriaName = function(element) {
    if (element.hasAttribute("aria-labelledby")) {
      let buffer = "";
      const ids = element.getAttribute("aria-labelledby").split(" ");
      for (let i = 0; i < ids.length; i++) {
        const label = document.getElementById(ids[i]);
        if (label) buffer = buffer + " " + trimmedInnerText(label);
      }
      return buffer.trim();
    }
    if (element.hasAttribute("aria-label")) {
      return element.getAttribute("aria-label");
    }
    if (element.hasAttribute("id")) {
      const label_id = element.getAttribute("id");
      let label = "";
      const labels = document.querySelectorAll("label[for='" + label_id + "']");
      for (let j = 0; j < labels.length; j++) label += labels[j].innerText + " ";
      label = label.trim();
      if (label !== "") return label;
    }
    if (element.parentElement && element.parentElement.tagName === "LABEL") {
      return element.parentElement.innerText;
    }
    if (element.hasAttribute("alt")) return element.getAttribute("alt");
    if (element.hasAttribute("title")) return element.getAttribute("title");
    return trimmedInnerText(element);
  };
  const getApproximateAriaRole = function(element) {
    let tag = element.tagName.toLowerCase();
    if (tag === "input" && element.hasAttribute("type")) {
      tag = tag + ", type=" + element.getAttribute("type");
    }
    if (element.hasAttribute("role")) {
      return [element.getAttribute("role"), tag];
    } else if (tag in roleMapping) {
      return [roleMapping[tag], tag];
    } else {
      return ["", tag];
    }
  };
  // === Class helpers ===
  const parseClassInput = function(v) {
    if (!v) return [];
    if (Array.isArray(v)) {
      let out = [];
      for (let i = 0; i < v.length; i++) out = out.concat(parseClassInput(v[i]));
      return out;
    }
    if (typeof v === "string") {
      return v
        .split(/[,\s]+/)
        .map(s => s.replace(/^\./, "").trim())
        .filter(Boolean);
    }
    return [];
  };
  const getClassTokens = function(node) {
    const clsList = node.classList;
    if (clsList && clsList.length) return Array.from(clsList);
    const raw = node.className;
    const str = (raw && raw.baseVal) ? raw.baseVal : (typeof raw === "string" ? raw : "");
    if (!str) return [];
    return str.split(/\s+/).filter(Boolean);
  };
  // mode: "all" | "any"; scope: "self" | "ancestors" | "self-or-ancestors"
  const elementMatchesClasses = function(element, classNames, mode, scope) {
    if (!element || classNames.length === 0) return false;
    const checkNode = (node) => {
      const set = new Set(getClassTokens(node));
      if (mode === "all") {
        for (let i = 0; i < classNames.length; i++) {
          if (!set.has(classNames[i])) return false;
        }
        return true;
      } else {
        for (let i = 0; i < classNames.length; i++) {
          if (set.has(classNames[i])) return true;
        }
        return false;
      }
    };
    if (scope === "self") {
      return checkNode(element);
    } else if (scope === "ancestors") {
      let node = element.parentElement;
      while (node) {
        if (checkNode(node)) return true;
        node = node.parentElement;
      }
      return false;
    } else {
      let node = element;
      while (node) {
        if (checkNode(node)) return true;
        node = node.parentElement;
      }
      return false;
    }
  };
  // defaults: includeMode="all", includeScope="self", excludeMode="any", excludeScope="self-or-ancestors"
  const filterElementsByClass = function(elements, includeClasses, excludeClasses, opts) {
    opts = opts || {};
    const includeMode = (opts.includeMode === "any" || opts.includeMode === "all") ? opts.includeMode : "all";
    const includeScope = (opts.includeScope === "self" || opts.includeScope === "ancestors" || opts.includeScope === "self-or-ancestors") ? opts.includeScope : "self";
    const excludeMode = (opts.excludeMode === "all" || opts.excludeMode === "any") ? opts.excludeMode : "any";
    const excludeScope = (opts.excludeScope === "self" || opts.excludeScope === "ancestors" || opts.excludeScope === "self-or-ancestors") ? opts.excludeScope : "self-or-ancestors";
    const out = [];
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      if (excludeClasses && excludeClasses.length > 0) {
        if (elementMatchesClasses(el, excludeClasses, excludeMode, excludeScope)) continue;
      }
      if (includeClasses && includeClasses.length > 0) {
        if (!elementMatchesClasses(el, includeClasses, includeMode, includeScope)) continue;
      }
      out.push(el);
    }
    return out;
  };
  // === ARIA / Role filter helpers ===
  const parseListInput = function(v) {
    if (v == null) return [];
    if (Array.isArray(v)) return v.map(s => String(s).trim()).filter(Boolean);
    if (typeof v === "string") return v.split(",").map(s => s.trim()).filter(Boolean);
    return [];
  };
  const parseRegexFromString = function(s) {
    if (typeof s !== "string") return null;
    if (s.length >= 2 && s[0] === "/" && s.lastIndexOf("/") > 0) {
      const last = s.lastIndexOf("/");
      const body = s.slice(1, last);
      const flags = s.slice(last + 1) || "i";
      try { return new RegExp(body, flags); } catch(e) { return null; }
    }
    return null;
  };
  const matchesRole = function(role, patterns) {
    const r = String(role || "");
    if (!patterns || patterns.length === 0) return true;
    for (let i = 0; i < patterns.length; i++) {
      const p = patterns[i];
      const re = parseRegexFromString(p);
      if (re) { if (re.test(r)) return true; }
      else if (r.toLowerCase() === String(p).toLowerCase()) { return true; }
    }
    return false;
  };
  const matchesAriaName = function(name, patterns) {
    const n = String(name || "");
    if (!patterns || patterns.length === 0) return true;
    for (let i = 0; i < patterns.length; i++) {
      const p = patterns[i];
      const re = parseRegexFromString(p);
      if (re) { if (re.test(n)) return true; }
      else if (n.toLowerCase().includes(String(p).toLowerCase())) { return true; }
    }
    return false;
  };
  // NEW: aria-valuetext helpers
  const getAriaValueText = function(element) {
    return element.getAttribute("aria-valuetext") || "";
  };
  const matchesAriaValueText = function(valueText, patterns) {
    const v = String(valueText || "");
    if (!patterns || patterns.length === 0) return true;
    for (let i = 0; i < patterns.length; i++) {
      const p = patterns[i];
      const re = parseRegexFromString(p);
      if (re) { if (re.test(v)) return true; }
      else if (v.toLowerCase().includes(String(p).toLowerCase())) { return true; }
    }
    return false;
  };
  const filterElementsByAriaAndRole = function(elements, includeRoles, excludeRoles, includeAria, excludeAria) {
    const out = [];
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      const role = getApproximateAriaRole(el)[0] || "";
      const name = getApproximateAriaName(el) || "";
      if (excludeRoles.length && matchesRole(role, excludeRoles)) continue;
      if (excludeAria.length && matchesAriaName(name, excludeAria)) continue;
      if (includeRoles.length && !matchesRole(role, includeRoles)) continue;
      if (includeAria.length && !matchesAriaName(name, includeAria)) continue;
      out.push(el);
    }
    return out;
  };
  // === Viewport helpers ===
  const isRectFullyInViewport = function(rect) {
    if (!rect || rect.width * rect.height === 0) return false;
    return (
      rect.left >= 0 &&
      rect.top >= 0 &&
      rect.right <= window.innerWidth &&
      rect.bottom <= window.innerHeight
    );
  };
  const isRectPartiallyInViewport = function(rect) {
    if (!rect || rect.width * rect.height === 0) return false;
    return (
      rect.right > 0 &&
      rect.bottom > 0 &&
      rect.left < window.innerWidth &&
      rect.top < window.innerHeight
    );
  };
  // === Record builder ===
  const buildRecord = function(currentElement) {
    const key = currentElement.getAttribute("__elementId");
    const rects = currentElement.getClientRects();
    const ariaRole = getApproximateAriaRole(currentElement);
    const ariaName = getApproximateAriaName(currentElement);
    const vScrollable = currentElement.scrollHeight - currentElement.clientHeight >= 1;
    let standardId = currentElement.id;
    if (!standardId) {
      const closestParentWithId = currentElement.closest("[id]");
      if (closestParentWithId) standardId = closestParentWithId.id;
    }
    const rawClass = currentElement.className;
    const classNameStr = (rawClass && rawClass.baseVal)
      ? rawClass.baseVal
      : (typeof rawClass === "string" ? rawClass : "");
    const record = {
      "element_id": key,
      "id": standardId,
      "tag_name": ariaRole[1],
      "role": ariaRole[0],
      "aria_name": ariaName,
      "class_name": classNameStr,
      "v-scrollable": vScrollable,
      "rects": []
    };
    for (const rect of rects) {
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      if (isTopmost(currentElement, x, y)) {
        record["rects"].push(JSON.parse(JSON.stringify(rect)));
      }
    }
    return record;
  };
  // === Core collectors ===
  const _getInteractiveRectsAll = function() {
    labelElements(getInteractiveElements());
    const elements = document.querySelectorAll("[__elementId]");
    const results = {};
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      const record = buildRecord(el);
      if (record.rects && record.rects.length > 0) results[record.element_id] = record;
    }
    return results;
  };
  const getInteractiveRects = function() {
    labelElements(getInteractiveElements());
    const elements = document.querySelectorAll("[__elementId]");
    const results = {};
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      const b = el.getBoundingClientRect();
      if (!isRectFullyInViewport(b)) continue;
      const cx = b.left + b.width / 2;
      const cy = b.top + b.height / 2;
      if (!isTopmost(el, cx, cy)) continue;
      const record = buildRecord(el);
      if (record.rects && record.rects.length > 0) results[record.element_id] = record;
    }
    return results;
  };
  // === Exports ===
  return {
    getInteractiveRects: getInteractiveRects,
    getInteractiveRectsAll: _getInteractiveRectsAll
  };
})();