from discovery import _should_include_product


def test_should_include_product_filters_plushes_and_card_accessories():
    assert _should_include_product("Pokémon TCG: Mega Evolution— Ascended Heroes Booster Bundle") is True
    assert _should_include_product("Pokémon Plush Pikachu") is False
    assert _should_include_product("Pokémon TCG Baraja de Cartas") is False
    assert _should_include_product("Pokémon TCG Card Sleeves Cover") is False
