from constellation_control.preview.operator_tabs import OPERATOR_TABS_CARD, OPERATOR_TABS_SCRIPT


def test_inputs_workspace_has_explicit_engineering_groups() -> None:
    assert 'id="operatorInputOfficialSources"' in OPERATOR_TABS_CARD
    assert 'id="operatorInputManualState"' in OPERATOR_TABS_CARD
    assert 'id="operatorInputSynthesis"' in OPERATOR_TABS_CARD
    assert 'id="operatorInputBulk"' in OPERATOR_TABS_CARD


def test_operator_layout_uses_semantic_card_anchors_not_position_or_text_regex() -> None:
    assert "operatorAdoptCardByChild('title','scenarioSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('fleet','constellationSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('geometry','geometrySummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('operations','operationsSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "unassigned[0]" not in OPERATOR_TABS_SCRIPT
    assert "operatorFallbackTarget" not in OPERATOR_TABS_SCRIPT


def test_runtime_progress_is_never_part_of_input_workspace() -> None:
    expected = "['operationsSummaryCard','runProgressCard','runPromotionCard','resourceStateCard','driftConsistencyCard'].forEach(id=>operatorMoveCard(id,'operatorTabResults'))"
    assert expected in OPERATOR_TABS_SCRIPT
    assert "operatorMoveCard('runProgressCard','operatorTabInputs')" not in OPERATOR_TABS_SCRIPT
