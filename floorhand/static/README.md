# Floorhand web app (no build step)

Plain ES modules served from `/static`. Open `index.html`; it loads `js/app.js`.

```
static/
  index.html            shell: #app, #sheet (dialog), #toasts, #answer (full-screen answer)
  css/tokens.css        design tokens ("Linen & Steel"): light + dark, type, spacing, motion
  css/ui.css            components (buttons, cards, stage rail, tables, sheet…) and screen layouts
  js/core.js            api(), state, i18n (addStrings/t/name), formatting, router, flash/toast
  js/ui.js              HTML-string components: icon(), btn(), chip(), stageLine(), table(), sheet()…
  js/app.js             the shell around a screen (sidebar on desktop, bottom tabs on phone)
  js/screens/index.js   the registry: one import line per screen
  js/screens/<id>.js    one screen = one file
  print/                stylesheets for printed documents (delivery note)
```

## Adding a screen

1. Create `js/screens/<id>.js`:

```js
import { registerScreen, addStrings, api, t } from "../core.js";
import { btn, empty } from "../ui.js";

addStrings({ en: { helloTitle: "Hello" }, ru: { helloTitle: "Привет" } });  // every string, both languages

registerScreen({
  id: "hello",                       // route: #/hello  (and #/hello/<id> with params: ["id"])
  roles: ["owner"],                  // "worker" | "tablet" | "owner" | "manager" | "office" (= owner + manager)
  icon: "spark",                     // a name from ui.js PATHS (inline SVG, no emoji)
  title: lang => lang === "ru" ? "Привет" : "Hello",
  nav: true,                         // false = reachable by go(), not in the menu
  async render(root, ctx) {          // ctx = { me, role, params, go, refresh, t, lang }
    const r = await api("/api/hello");
    root.innerHTML = r.ok ? `<h1>${t("helloTitle")}</h1>` : empty(r.message, "alert");
  },
});
```

2. Add `import "./hello.js";` to `js/screens/index.js`. Order there = order in the navigation.

## Rules the front-end keeps (tests/test_static.py)
- Every `addStrings` bundle has the same keys in `en` and `ru`.
- No emoji in the UI: icons are inline SVG from `ui.js`.
- Every colour token has a dark-mode value.
- Every action answers: `flash(ok, text)` (full screen, sound, haptic) or `toast(text)`; refusals stay until read.
- Money is never formatted on the client for a shared tablet: the server does not send it.
- Own dialog (`sheet()`), never `confirm()` / `prompt()`.
