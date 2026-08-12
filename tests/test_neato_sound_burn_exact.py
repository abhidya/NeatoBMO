from tools.neato_sound_burn_exact import ALLOWED_SHA256, matching_version


def test_silent_bank_is_hash_locked():
    assert ALLOWED_SHA256[
        "ebce7f200a8a3f5f0676c475b7abb3aba5926ce559fb81bd3c3e0f37c042449a"
    ] == "validated-all-slots-silent-pcm-only"


def test_exact_target_accepts_installed_and_factory_updaters():
    identity = b"Serial Number,WTD41611DD,0037829,P\r\n"
    assert matching_version(identity + b"Software,2,4,15667\r\n")
    assert matching_version(identity + b"Software,2,5,15893\r\n")
    assert not matching_version(identity + b"Software,3,2,18755\r\n")
    assert not matching_version(
        b"Serial Number,OTHER,ROBOT,P\r\nSoftware,2,5,15893\r\n"
    )
