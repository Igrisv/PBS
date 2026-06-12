from notifier import resolve_bridge_chat_id, resolve_bridge_chat_ids


def test_resolve_bridge_chat_id_uses_specific_channel_for_ascended_heroes():
    assert resolve_bridge_chat_id(
        product_name="Pokémon TCG: Mega Evolution— Ascended Heroes Booster Bundle",
        default_chat_id="general-group",
        ascended_chat_id="ascended-group",
    ) == "ascended-group"


def test_resolve_bridge_chat_id_keeps_default_channel_for_other_products():
    assert resolve_bridge_chat_id(
        product_name="Pokémon TCG: Charizard ex Box",
        default_chat_id="general-group",
        ascended_chat_id="ascended-group",
    ) == "general-group"


def test_resolve_bridge_chat_ids_sends_only_to_special_group_for_ascended_heroes():
    assert resolve_bridge_chat_ids(
        product_name="Pokémon TCG: Mega Evolution— Ascended Heroes Booster Bundle",
        default_chat_id="general-group",
        ascended_chat_id="ascended-group",
    ) == ["ascended-group"]


def test_resolve_bridge_chat_ids_keeps_default_only_for_other_products():
    assert resolve_bridge_chat_ids(
        product_name="Pokémon TCG: Charizard ex Box",
        default_chat_id="general-group",
        ascended_chat_id="ascended-group",
    ) == ["general-group"]
