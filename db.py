import os
from functools import lru_cache

import httpx
from supabase import Client, ClientOptions, create_client

# Matching a trial fires one Supabase request per criterion rule per
# candidate patient -- easily 1000+ sequential requests for a single
# /db-candidates call. Postgrest's default client talks HTTP/2, and under
# that much sustained sequential load the connection occasionally gets
# dropped mid-stream by the network path to Supabase, surfacing as
# httpx.RemoteProtocolError("Server disconnected") and a bare 500. Supplying
# our own httpx.Client pins the connection to HTTP/1.1 (sidestepping that
# HTTP/2 code path) and lets the transport transparently retry a request
# that fails at the connection level -- every one of these calls is a plain
# read (or an idempotent delete/insert keyed by patient+trial), so retrying
# is safe.
_HTTPX_CLIENT = httpx.Client(transport=httpx.HTTPTransport(retries=3), timeout=30)


@lru_cache
def get_client() -> Client:
    url = os.environ["DATABASE_URL"]
    key = os.environ["SUPABASE_ANON_KEY"]
    return create_client(url, key, options=ClientOptions(httpx_client=_HTTPX_CLIENT))
