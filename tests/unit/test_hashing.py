from io_pipeline.hashing import compute_sha256


def test_sha256_returns_64_char_hex() -> None:
    result = compute_sha256(b"hello")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_sha256_known_value() -> None:
    assert (
        compute_sha256(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_sha256_deterministic() -> None:
    assert compute_sha256(b"abc") == compute_sha256(b"abc")


def test_sha256_different_inputs_differ() -> None:
    assert compute_sha256(b"a") != compute_sha256(b"b")
