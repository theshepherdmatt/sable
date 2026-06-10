"""Small state machine. States are Screen instances; transitions are declarative.

TABLE is the single source of truth for event-driven transitions -- no transition
fires that is not listed here, and every target is a registered screen. The token
"@nowplaying" resolves (via the app) to the configured now-playing screen
(modern, spectrum, ...), so adding a now-playing variant needs no new transitions.

Carry-forward #8: locks are released BEFORE on_exit/on_enter fire, because a
screen's enter can itself request a transition.
"""
import threading

NOWPLAYING = "@nowplaying"

# current_state -> { event -> target_state }
TABLE = {
    "splash":   {"ready": "clock"},
    "clock":    {"play": NOWPLAYING, "menu": "menu"},
    # now-playing: a select opens the menu (no longer dumps to clock); stop -> clock
    # is the only auto-exit. App.reconcile_screen() backstops any missed play edge.
    "modern":   {"stop": "clock", "menu": "menu"},
    "spectrum": {"stop": "clock", "menu": "menu"},
    "menu":     {"back": "clock", "timeout": "clock"},
}


class FSM:
    def __init__(self, app, screens, menu_inactivity_s=15.0, log=print):
        self.app = app
        self.screens = {s.name: s for s in screens}
        self.current = None
        self.log = log
        self._lock = threading.RLock()
        self._menu_timer = None
        self.menu_inactivity_s = menu_inactivity_s

    def go(self, name, **kwargs):
        target = self.screens[name]
        with self._lock:
            prev = self.current
            if prev is target:
                return
        if prev is not None:
            prev.on_exit()
        with self._lock:
            self.current = target
        self._cancel_menu_timer()
        target.on_enter(**kwargs)
        self.app.render()

    def dispatch(self, event):
        """Apply a table-driven transition for `event` from the current state."""
        cur = self.current.name if self.current is not None else None
        target = TABLE.get(cur, {}).get(event)
        if target == NOWPLAYING:
            target = self.app.nowplaying_screen()
        if target is not None:
            self.log("fsm: %s --%s--> %s" % (cur, event, target))
            self.go(target)
        return target

    # --- menu inactivity (menu -> clock after N seconds idle) ---
    def reset_menu_timer(self):
        self._cancel_menu_timer()
        self._menu_timer = threading.Timer(self.menu_inactivity_s, self._menu_expired)
        self._menu_timer.daemon = True
        self._menu_timer.start()

    def _cancel_menu_timer(self):
        if self._menu_timer:
            self._menu_timer.cancel()
            self._menu_timer = None

    def _menu_expired(self):
        if self.current is not None and self.current.name == "menu":
            self.dispatch("timeout")
