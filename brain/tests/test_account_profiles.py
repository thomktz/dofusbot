from dofus_brain.world.account_profiles import AccountProfileStore


def test_profiles_are_public_metadata_with_separate_local_token_paths(tmp_path):
    store = AccountProfileStore(tmp_path)
    profile = store.create("Cra Main")
    assert profile.id == "cra-main"
    assert profile.token_path == tmp_path / "accounts" / "cra-main.json"
    assert store.list()[0].public() == {"id": "cra-main", "label": "Cra Main", "authenticated": False}


def test_profiles_reject_duplicate_labels_after_slugging(tmp_path):
    store = AccountProfileStore(tmp_path)
    store.create("Cra Main")
    try:
        store.create("cra-main")
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate profile must not overwrite credentials")
