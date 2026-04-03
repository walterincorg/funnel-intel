from supabase import create_client, Client
from backend.config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_STORAGE_BUCKET

_client: Client | None = None


def get_db() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client
