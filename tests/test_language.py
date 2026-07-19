"""Language normalization tests."""

from __future__ import annotations

from media_insights.language import display_name, normalize_language


def test_alpha3_normalizes_to_alpha2() -> None:
    info = normalize_language("jpn")
    assert info is not None
    assert info.raw == "jpn"
    assert info.normalized == "ja"


def test_alpha2_passthrough() -> None:
    info = normalize_language("en")
    assert info is not None
    assert info.raw == "en"
    assert info.normalized == "en"


def test_alpha3_eng_normalizes() -> None:
    info = normalize_language("eng")
    assert info is not None
    assert info.raw == "eng"
    assert info.normalized == "en"


def test_region_tagged_locale_collapses_to_base_language() -> None:
    info = normalize_language("pt-BR")
    assert info is not None
    assert info.raw == "pt-BR"
    assert info.normalized == "pt"


def test_underscore_locale_also_normalizes() -> None:
    info = normalize_language("pt_BR")
    assert info is not None
    assert info.raw == "pt_BR"
    assert info.normalized == "pt"


def test_bibliographic_code_fallback() -> None:
    """'fre' is the legacy ISO-639-2/B code for French; fromietf() alone
    doesn't resolve it, only the fromalpha3b() fallback does."""
    info = normalize_language("fre")
    assert info is not None
    assert info.raw == "fre"
    assert info.normalized == "fr"


def test_case_insensitive() -> None:
    info = normalize_language("ENG")
    assert info is not None
    assert info.normalized == "en"


def test_unknown_token_has_no_normalized_form_but_keeps_raw() -> None:
    info = normalize_language("not-a-real-language-xyz")
    assert info is not None
    assert info.raw == "not-a-real-language-xyz"
    assert info.normalized is None


def test_full_name_resolves_via_fromname_fallback() -> None:
    """Neither fromietf() nor fromalpha3b() know 'portuguese' -- only the
    fromname() fallback does. This is what lets a subtitle-coverage search
    for "Portuguese" match files tagged pt/pt-BR/por interchangeably."""
    info = normalize_language("portuguese")
    assert info is not None
    assert info.normalized == "pt"
    assert normalize_language("Portuguese").normalized == "pt"
    assert normalize_language("PORTUGUESE").normalized == "pt"


def test_name_with_no_alpha2_falls_back_to_alpha3() -> None:
    """Klingon is resolvable by name but has no ISO 639-1 (alpha2) code --
    babelfish raises on lang.alpha2 for these instead of returning falsy, so
    normalize_language must catch that and fall back to alpha3 ('tlh')."""
    info = normalize_language("klingon")
    assert info is not None
    assert info.normalized == "tlh"


def test_none_and_blank_return_none() -> None:
    assert normalize_language(None) is None
    assert normalize_language("") is None
    assert normalize_language("   ") is None


def test_display_name_known_codes() -> None:
    assert display_name("en") == "English"
    assert display_name("ja") == "Japanese"
    assert display_name("pt") == "Portuguese"


def test_display_name_unknown_or_none() -> None:
    assert display_name(None) is None
    assert display_name("") is None
