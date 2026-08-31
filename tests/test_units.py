"""Human formatting. These strings are the app's whole vocabulary for
numbers, and they follow the XP/7 original rather than SI convention."""

import pytest

from tanksmanager.backend.units import (bytes_h, bytes_pair, kib, nice_label,
                                        percent, rate)


@pytest.mark.parametrize("value,expected", [
    (0, "0 B"),
    (512, "512 B"),
    (1024, "1.0 KiB"),
    (1536, "1.5 KiB"),
    (1024 ** 2, "1.0 MiB"),
    (1024 ** 3 * 15.4, "15.4 GiB"),
    (1024 ** 4, "1.0 TiB"),
])
def test_bytes_are_binary_not_decimal(value, expected):
    assert bytes_h(value) == expected


def test_kib_follows_the_task_manager_column(value=123456 * 1024):
    # Windows showed process memory as "123,456 K", and so does the table.
    assert kib(value) == "123,456 K"


def test_bytes_pair_prints_a_shared_unit_once():
    # "1.9/15.4 GiB" is what makes the reading fit on a card.
    assert bytes_pair(1024 ** 3 * 1.9, 1024 ** 3 * 15.4) == "1.9/15.4 GiB"


def test_bytes_pair_keeps_both_units_when_they_differ():
    assert bytes_pair(900, 1024 ** 3) == "900 B / 1.0 GiB"


def test_an_idle_rate_prints_nothing_rather_than_zero():
    # A table full of "0 B/s" is noise; the original left the cell blank.
    assert rate(0) == ""
    assert rate(2048) == "2.0 KiB/s"


def test_percent_has_a_space_before_the_sign():
    assert percent(42.4) == "42 %"
    assert percent(42.44, 1) == "42.4 %"


@pytest.mark.parametrize("nice,expected", [
    (-20, "Realtime (-20)"),
    (-10, "High (-10)"),
    (-1, "Above normal (-1)"),
    (0, "Normal"),
    (5, "Below normal (+5)"),
    (19, "Low (+19)"),
])
def test_nice_values_map_onto_the_xp_vocabulary(nice, expected):
    # The table shows "Below normal", not "5", because that is the word the
    # Set Priority menu uses.
    assert nice_label(nice) == expected
