from driftbench.checks import run_check


def test_max_words():
    assert run_check({"type": "max_words", "value": 3}, "one two three")[0]
    assert not run_check({"type": "max_words", "value": 3}, "one two three four")[0]


def test_no_list():
    assert run_check({"type": "no_list"}, "Just a paragraph. With 2 sentences.")[0]
    assert not run_check({"type": "no_list"}, "Tips:\n1. sleep\n2. practice")[0]
    assert not run_check({"type": "no_list"}, "- a\n- b")[0]


def test_normalized_equals_ignores_quotes_and_period():
    c = {"type": "normalized_equals", "value": "TaskNest"}
    assert run_check(c, '"TaskNest".')[0]
    assert run_check(c, "  tasknest ")[0]
    assert not run_check(c, "Taskly")[0]


def test_not_contains_any_uses_word_boundaries():
    c = {"type": "not_contains_any", "value": ["egg"]}
    assert run_check(c, "roasted eggplant")[0]
    assert not run_check(c, "add one egg")[0]
    assert not run_check(c, "scrambled eggs")[0]


def test_ends_with_question():
    assert run_check({"type": "ends_with_question"}, "A stack is LIFO. **What does pop do?**")[0]
    assert not run_check({"type": "ends_with_question"}, "A stack is LIFO.")[0]
