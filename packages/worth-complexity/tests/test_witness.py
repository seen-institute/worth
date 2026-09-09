"""The witness bundle: canonical hashing, HMAC, and what changes the digest."""

from __future__ import annotations

from worth_complexity.witness import JsonValue, input_hash, witness


def test_input_hash_is_stable_regardless_of_file_order() -> None:
    files_a = [("clinical/a.txt", "hash1"), ("remittance/b.txt", "hash2")]
    files_b = list(reversed(files_a))
    assert input_hash(files_a) == input_hash(files_b)


def test_input_hash_changes_with_content() -> None:
    a = input_hash([("a.txt", "hash1")])
    b = input_hash([("a.txt", "hash2")])
    assert a != b


def test_digest_is_stable_across_dict_key_order() -> None:
    outputs_a: JsonValue = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
    outputs_b: JsonValue = {"a": 1, "nested": {"x": 1, "y": 2}, "b": 2}
    pv_a = {"worth-complexity": "0.1.0", "worth-fees": "0.1.0"}
    pv_b = {"worth-fees": "0.1.0", "worth-complexity": "0.1.0"}
    w1 = witness(
        input_hash="ih",
        rulebook_version="rb@1",
        weights_version="w1",
        package_versions=pv_a,
        outputs=outputs_a,
        key=None,
    )
    w2 = witness(
        input_hash="ih",
        rulebook_version="rb@1",
        weights_version="w1",
        package_versions=pv_b,
        outputs=outputs_b,
        key=None,
    )
    assert w1.digest == w2.digest
    assert w1.outputs_hash == w2.outputs_hash


def test_scheme_is_sha256_with_no_key() -> None:
    w = witness(
        input_hash="ih",
        rulebook_version="rb",
        weights_version="w",
        package_versions={},
        outputs={},
        key=None,
    )
    assert w.scheme == "sha256"
    assert w.signature is None


def test_scheme_is_hmac_sha256_with_a_key() -> None:
    w = witness(
        input_hash="ih",
        rulebook_version="rb",
        weights_version="w",
        package_versions={},
        outputs={},
        key=b"secret",
    )
    assert w.scheme == "hmac-sha256"
    assert w.signature is not None


def test_different_keys_give_different_signatures_but_the_same_digest() -> None:
    a = witness(
        input_hash="ih",
        rulebook_version="rb",
        weights_version="w",
        package_versions={},
        outputs={"x": 1},
        key=b"key1",
    )
    b = witness(
        input_hash="ih",
        rulebook_version="rb",
        weights_version="w",
        package_versions={},
        outputs={"x": 1},
        key=b"key2",
    )
    assert a.signature != b.signature
    assert a.digest == b.digest


def test_different_outputs_give_a_different_digest() -> None:
    a = witness(
        input_hash="ih",
        rulebook_version="rb",
        weights_version="w",
        package_versions={},
        outputs={"x": 1},
        key=None,
    )
    b = witness(
        input_hash="ih",
        rulebook_version="rb",
        weights_version="w",
        package_versions={},
        outputs={"x": 2},
        key=None,
    )
    assert a.digest != b.digest
    assert a.outputs_hash != b.outputs_hash


def test_same_call_twice_is_identical() -> None:
    kwargs = {
        "input_hash": "ih",
        "rulebook_version": "rb@1",
        "weights_version": "w1",
        "package_versions": {"worth-complexity": "0.1.0"},
        "outputs": {"a": [1, 2, 3]},
        "key": b"k",
    }
    a = witness(**kwargs)  # type: ignore[arg-type]
    b = witness(**kwargs)  # type: ignore[arg-type]
    assert a == b
