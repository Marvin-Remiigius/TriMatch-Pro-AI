import os
from functools import lru_cache

from supabase import Client, create_client


@lru_cache
def get_client() -> Client:
    url = os.environ["DATABASE_URL"]
    key = os.environ["SUPABASE_ANON_KEY"]
    return create_client(url, key)
