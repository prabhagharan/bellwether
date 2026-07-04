from bellwether.security.passwords import hash_password, verify_password

def test_hash_and_verify():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False
