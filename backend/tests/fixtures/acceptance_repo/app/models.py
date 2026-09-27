"""User model for the acceptance fixture repository."""


class User:
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

    def to_dict(self):
        return {"id": self.id, "username": self.username}


_USERS_BY_ID = {}


def save_user(user):
    _USERS_BY_ID[user.id] = user
    return user


def get_user_by_id(user_id):
    return _USERS_BY_ID.get(user_id)
