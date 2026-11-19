/**
 * The entry point a customer's page loads.
 *
 * It does exactly one thing, and the order matters:
 * ``document.currentScript`` is only the loading script *while the module is
 * being evaluated*. Reading it later -- inside a callback, after an ``await``,
 * once React has mounted -- gives null, and the widget would lose the ids it
 * was configured with. So it is captured here, on the first line that runs,
 * and handed to the mounter.
 */

import { mountWidget } from "./mount";

const script = document.currentScript;

mountWidget(script instanceof HTMLScriptElement ? script : null);
