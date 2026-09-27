from app.implementation.patch_generator import generate_patch


def test_generate_patch_combines_model_patch_and_new_files():
    model_patch = (
        "--- a/backend/app/api/users.py\n"
        "+++ b/backend/app/api/users.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def get_user():\n"
        "+    require_auth()\n"
        "     return {}\n"
    )
    files_to_create = [
        {"path": "backend/app/auth/jwt_utils.py", "content": "def encode_jwt(p):\n    return p\n", "reason": "new JWT helper"},
    ]
    result = generate_patch(model_patch, files_to_create)
    assert result.is_syntactically_valid
    assert "backend/app/api/users.py" in result.files_touched
    assert "backend/app/auth/jwt_utils.py" in result.files_touched
    assert result.new_files_added_deterministically == ["backend/app/auth/jwt_utils.py"]


def test_generate_patch_does_not_duplicate_new_file_already_in_model_patch():
    model_patch = (
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+print('hi')\n"
    )
    files_to_create = [{"path": "new.py", "content": "print('hi')\n", "reason": "x"}]
    result = generate_patch(model_patch, files_to_create)
    assert result.new_files_added_deterministically == []
    assert result.patch_text.count("+++ b/new.py") == 1


def test_generate_patch_empty_inputs():
    result = generate_patch("", [])
    assert result.is_syntactically_valid
    assert result.patch_text == ""
    assert result.files_touched == []


def test_generate_patch_flags_invalid_model_patch():
    result = generate_patch("garbage patch text", [])
    assert not result.is_syntactically_valid
    assert result.validation_errors
