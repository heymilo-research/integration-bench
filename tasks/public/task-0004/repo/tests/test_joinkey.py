from staffline_placemint_merge.joinkey import join_key


def test_join_key_normalizes_case_and_whitespace():
    assert join_key("Ada  Babbage") == "ada babbage"
    assert join_key("  ada babbage ") == "ada babbage"
    assert join_key("ADA BABBAGE") == "ada babbage"


def test_join_key_distinguishes_different_names():
    assert join_key("Ada Babbage") != join_key("Ada Curie")
