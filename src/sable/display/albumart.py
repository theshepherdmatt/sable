"""Album-art cache.

Fetches OFF the render tick (a background thread), caches results, and returns
None until/unless the image is ready -- so drawing never blocks on the network.
Handles both absolute URLs (https://img.radioparadise.com/...) and Volumio-
relative paths (/albumart?...), prefixing the Volumio host for the latter.

Uses stdlib urllib (no requests dependency).
"""
import io
import threading
import urllib.request

from PIL import Image


class AlbumArtCache:
    def __init__(self, host="http://localhost:3000", size=(56, 56), timeout=5.0,
                 maxsize=16, log=print):
        self.host = host.rstrip("/")
        self.size = size
        self.timeout = timeout
        self.maxsize = maxsize
        self.log = log
        self._lock = threading.Lock()
        self._cache = {}      # url -> Image | False (failed)
        self._order = []      # insertion order for bounded eviction
        self._inflight = set()

    def _store(self, url, value):
        # bounded cache: evict oldest entries past maxsize (FIFO).
        self._cache[url] = value
        self._order.append(url)
        while len(self._order) > self.maxsize:
            old = self._order.pop(0)
            self._cache.pop(old, None)

    def resolve_url(self, art):
        if not art:
            return None
        if art.startswith("http://") or art.startswith("https://"):
            return art
        if not art.startswith("/"):
            art = "/" + art
        return self.host + art

    def get(self, art):
        """Return a cached PIL image, or None (and start a fetch) if not ready."""
        url = self.resolve_url(art)
        if url is None:
            return None
        with self._lock:
            if url in self._cache:
                val = self._cache[url]
                return val if val else None
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
            # LANCZOS downscale: covers are big (300-600px) shrinking to ~56px,
            # so resampling quality is what makes the greyscale art look sharp
            # rather than aliased. (The panel's own 16-level quantisation happens
            # later in the display backend; we keep full "L" here.)
            img = Image.open(io.BytesIO(data)).convert("L").resize(
                self.size, Image.LANCZOS)
            with self._lock:
                self._store(url, img)
            self.log("albumart: fetched", url)
        except Exception as exc:
            with self._lock:
                self._store(url, False)
            self.log("albumart: fetch failed:", exc)
        finally:
            with self._lock:
                self._inflight.discard(url)
