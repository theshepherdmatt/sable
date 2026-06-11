"""Album-art cache.

Fetches OFF the render tick (a background thread), caches results, and returns
None until/unless the image is ready -- so drawing never blocks on the network.
Handles both absolute URLs (https://img.radioparadise.com/...) and Volumio-
relative paths (/albumart?...), prefixing the Volumio host for the latter.

Uses stdlib urllib (no requests dependency).
"""
import io
import threading
import time
import urllib.request

from PIL import Image


class AlbumArtCache:
    def __init__(self, host="http://localhost:3000", size=(56, 56), mode="fit",
                 timeout=5.0, maxsize=16, retry_after=30.0, log=print):
        self.host = host.rstrip("/")
        self.size = size
        self.mode = mode          # "fit" = resize to size; "cover" = fill + crop
        self.timeout = timeout
        self.maxsize = maxsize
        self.retry_after = retry_after   # re-attempt a failed fetch after N seconds
        self.log = log
        self._lock = threading.Lock()
        self._cache = {}      # url -> Image | float(failure monotonic time)
        self._order = []      # insertion order for bounded eviction
        self._inflight = set()

    def _store(self, url, value):
        # bounded cache: evict oldest entries past maxsize (FIFO).
        self._cache[url] = value
        self._order.append(url)
        while len(self._order) > self.maxsize:
            old = self._order.pop(0)
            self._cache.pop(old, None)

    def _process(self, img):
        """LANCZOS resampling -- it is what makes the greyscale art look sharp
        rather than aliased. "fit" resizes square art to the box; "cover" scales
        to FILL the box then centre-crops (for the full-bleed Cinema theme, where
        a square cover backs a 256x64 panel -- we show a cinematic slice of it).
        The panel's 16-level quantisation happens later in the display backend."""
        tw, th = self.size
        if self.mode == "cover":
            s = max(tw / img.width, th / img.height)
            r = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                           Image.LANCZOS)
            x = (r.width - tw) // 2
            y = (r.height - th) // 2
            return r.crop((x, y, x + tw, y + th))
        return img.resize(self.size, Image.LANCZOS)

    def resolve_url(self, art):
        if not art:
            return None
        if art.startswith("http://") or art.startswith("https://"):
            return art
        if not art.startswith("/"):
            art = "/" + art
        return self.host + art

    def get(self, art):
        """Return a cached PIL image, or None (and start a fetch) if not ready.
        A FAILED fetch is remembered only for retry_after seconds, then retried --
        so a transient network blip on a station's art does not blank it for the
        whole session."""
        url = self.resolve_url(art)
        if url is None:
            return None
        with self._lock:
            val = self._cache.get(url)
            if isinstance(val, Image.Image):
                return val
            if val is not None and (time.monotonic() - val) < self.retry_after:
                return None                      # failed recently -> still cooling down
            if url in self._inflight:
                return None
            self._inflight.add(url)
        threading.Thread(target=self._fetch, args=(url,), daemon=True,
                         name="sable-albumart").start()
        return None

    def _fetch(self, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sable"})
            data = urllib.request.urlopen(req, timeout=self.timeout).read()
            img = self._process(Image.open(io.BytesIO(data)).convert("L"))
            with self._lock:
                self._store(url, img)
            self.log("albumart: fetched", url)
        except Exception as exc:
            with self._lock:
                self._store(url, time.monotonic())   # remember failure time (retryable)
            self.log("albumart: fetch failed:", exc)
        finally:
            with self._lock:
                self._inflight.discard(url)
