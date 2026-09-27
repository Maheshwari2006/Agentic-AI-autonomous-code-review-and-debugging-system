"""User API endpoints. `get_user` has no authentication dependency --
this is the specific gap the implementation feature should find."""
from .models import get_user_by_id


def get_user(user_id):
    """Return a user's public info. BUG: does not check whether the
    caller is authenticated at all."""
    user = get_user_by_id(user_id)
    if user is None:
        return {"error": "not found"}, 404
    return user.to_dict(), 200
