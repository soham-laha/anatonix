/**
 * Anatonix — Streamlit component bridge.
 *
 * A dependency-free implementation of the Streamlit custom-component protocol
 * (apiVersion 1).  The component posts `streamlit:*` messages to the host page:
 *
 *   componentReady      -> handshake, host replies with `streamlit:render`
 *   setFrameHeight      -> keeps the iframe exactly as tall as the UI
 *   setComponentValue   -> sends telemetry/events back to Python
 *
 * Python receives the value through `declare_component(...)` and stores it on
 * every rerun, so every payload is treated as re-deliverable (see
 * backend/session_manager.py, which de-duplicates by client uid).
 */

const RENDER_EVENT = 'streamlit:render';

function post(type, payload) {
  const message = Object.assign({ isStreamlitMessage: true, type }, payload || {});
  try {
    window.parent.postMessage(message, '*');
  } catch (error) {
    console.warn('[anatonix] bridge post failed', error);
  }
}

export class StreamlitBridge {
  constructor() {
    this.args = {};
    this.rendered = false;
    this.lastHeight = 0;
    this._renderHandlers = new Set();
    this._hostConnected = false;
    window.addEventListener('message', (event) => this._onMessage(event));
  }

  _onMessage(event) {
    const data = event.data;
    if (!data || typeof data !== 'object' || data.type !== RENDER_EVENT) return;
    this._hostConnected = true;
    this.args = data.args || {};
    this.rendered = true;
    for (const handler of this._renderHandlers) {
      try {
        handler(this.args);
      } catch (error) {
        console.error('[anatonix] render handler failed', error);
      }
    }
  }

  onRender(handler) {
    this._renderHandlers.add(handler);
    if (this.rendered) handler(this.args);
    return () => this._renderHandlers.delete(handler);
  }

  ready() {
    post('streamlit:componentReady', { apiVersion: 1 });
  }

  setFrameHeight(height) {
    const rounded = Math.max(120, Math.ceil(height));
    if (Math.abs(rounded - this.lastHeight) < 4) return;
    this.lastHeight = rounded;
    post('streamlit:setFrameHeight', { height: rounded });
  }

  /** Send a value back to Python (triggers one Streamlit rerun). */
  sendValue(value, dataType = 'json') {
    post('streamlit:setComponentValue', { value, dataType });
  }

  get connected() {
    return this._hostConnected;
  }
}

/**
 * Keep the iframe sized to its content.  Uses ResizeObserver when available and
 * falls back to a rAF-driven poll so the component never ends up clipped.
 */
export function autoSize(bridge, target = document.body) {
  const apply = () => {
    const height = Math.max(
      target.scrollHeight,
      target.offsetHeight,
      document.documentElement.scrollHeight
    );
    bridge.setFrameHeight(height + 6);
  };

  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(() => apply());
    observer.observe(target);
    apply();
    window.addEventListener('resize', apply);
  } else {
    apply();
    setInterval(apply, 700);
  }
  return apply;
}
